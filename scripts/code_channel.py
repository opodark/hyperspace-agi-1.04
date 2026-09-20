# SPDX-License-Identifier: Apache-2.0
"""Canale di conversazione fra agenti che scrivono codice (lato terminale).

Il canale esiste (docs/code-conversation.md) ma senza uno strumento si usa solo
scrivendo a mano un `curl` con il JSON dentro, e nessuno puo' *leggere* un filo
dal terminale. Questo script e' quell'interfaccia: la usano gli agenti sulle due
macchine e la si usa a mano per guardare cosa si sono detti.

Tre scelte che valgono piu' di tutto il resto:

  1. SOLO LIBRERIA STANDARD. Deve girare sul Mac e sul Windows del collega senza
     installare niente: stesso motivo di scripts/mesh_health.py.

  2. IL FILO NON SI PERDE. `push_log` genera un trace_id nuovo quando non gliene
     passi uno, quindi un messaggio senza trace diventa una conversazione di un
     messaggio solo — il modo piu' silenzioso di rovinare il canale. Qui
     `propose` CREA il filo e te lo stampa, mentre `review`/`verdict` lo
     PRETENDONO: se lo dimentichi te lo dice, invece di scrivere nel vuoto.

  3. IL VERDETTO NON E' UN'OPINIONE. `gate` esegue la suite vera su questo
     albero di lavoro e pubblica il `code_verdict` con l'esito reale: nessuno
     puo' scrivere "approvato" senza che i test siano passati. Il gate sui DIFF
     (workspace pulito + `git apply --check`) resta quello del nightly: questo e'
     il controllo che serve prima di un merge a mano.

Uso:

    python3 scripts/code_channel.py propose --to win11 --summary "estrai X" --artifact art-123
    python3 scripts/code_channel.py review  --trace a1b2c3d4 --from win11 --summary "ok ma..."
    python3 scripts/code_channel.py gate    --trace a1b2c3d4 --from win11
    python3 scripts/code_channel.py thread  --trace a1b2c3d4
    python3 scripts/code_channel.py threads

Sul Windows: `python scripts\\code_channel.py ...` (stessi argomenti).
"""
import argparse
import json
import os
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

TIPI = ("code_proposal", "code_review", "code_verdict")
TIPI_LABEL = {"code_proposal": "proposta", "code_review": "revisione", "code_verdict": "verdetto"}
CP_DEFAULT = os.environ.get("CONTROL_PLANE_URL", "http://localhost:8085").rstrip("/")
SUITE_DEFAULT = ("python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py")


def nodo_locale() -> str:
    """Nome con cui questo host si firma nei messaggi.

    `platform.node()` da' il nome di rete (MacBook-Air-di-Alberto-2.local): chi
    vuole l'alias della mesh (`macbook`, `win11`) usa HS_NODE_ALIAS, perche' il
    canale e' leggibile solo se i partecipanti si chiamano come nella dashboard.
    """
    return os.environ.get("HS_NODE_ALIAS") or platform.node().split(".")[0] or "unknown"


def log_field(riga: dict, *nomi: str) -> str:
    """Un campo di log qualunque sia la forma delle chiavi.

    `GET /logs` legge le righe del DB (source, target, trace_id, log_id) mentre
    `POST /logs/add` restituisce push_log (sourceNode, targetNode, traceId, id).
    La stessa trappola del bridge (infra-ui/server.py): leggere la chiave
    sbagliata non da' un errore, da' un campo vuoto.
    """
    for nome in nomi:
        valore = (riga or {}).get(nome)
        if valore not in (None, ""):
            return str(valore)
    return ""


def messaggio(kind: str, trace: str, source: str, target: str = "", summary: str = "",
              detail: str = "", status: str = "info") -> dict:
    """Il corpo per POST /logs/add. Valida il vocabolario prima di spedirlo."""
    if kind not in TIPI:
        raise ValueError(f"tipo non valido: {kind!r} (attesi {', '.join(TIPI)})")
    if not str(summary).strip():
        raise ValueError("summary obbligatorio: e' l'unica riga che si legge in dashboard")
    if not str(trace).strip():
        raise ValueError("trace mancante: senza filo il messaggio e' una conversazione da solo")
    return {"type": kind, "traceId": str(trace).strip(), "sourceNode": source,
            "targetNode": target, "summary": summary, "detail": detail, "status": status}


def group_threads(righe: list) -> list:
    """Raggruppa per `trace`: fili piu' recenti prima, messaggi dentro per ts.

    Stessa semantica di groupCodeThreads nella dashboard: due implementazioni
    della stessa regola (browser e terminale), quindi il test le prova entrambe
    sullo stesso caso, perche' una divergenza fra le due sarebbe silenziosa.
    """
    fili: dict = {}
    for riga in righe or []:
        trace = log_field(riga, "traceId", "trace_id").strip()
        if not trace:
            continue
        filo = fili.setdefault(trace, {"trace": trace, "messages": [], "participants": [],
                                       "verdict": "", "last_ts": ""})
        filo["messages"].append(riga)
        chi = log_field(riga, "sourceNode", "source")
        if chi and chi not in filo["participants"]:
            filo["participants"].append(chi)
        if log_field(riga, "type") == "code_verdict":
            filo["verdict"] = log_field(riga, "status")
        ts = log_field(riga, "ts", "timestamp")
        if ts > filo["last_ts"]:
            filo["last_ts"] = ts
    for filo in fili.values():
        filo["messages"].sort(key=lambda r: log_field(r, "ts", "timestamp"))
    return sorted(fili.values(), key=lambda f: f["last_ts"], reverse=True)


def summarize_suite(saida: str) -> str:
    """La riga che riassume una corsa della suite, per il `detail` del verdetto.

    Si cerca l'esito REALE di unittest ("Ran N tests", "OK", "FAILED (...)"):
    l'ultima riga qualsiasi non basta, un traceback o un print finale mentirebbero.
    """
    testo = str(saida or "")
    ran = re.search(r"^Ran (\d+) test", testo, re.M)
    ok = re.search(r"^OK\b", testo, re.M)
    fallito = re.search(r"^FAILED \((.+)\)", testo, re.M)
    parti = []
    if ran:
        parti.append(f"{ran.group(1)} test")
    if ok:
        parti.append("OK")
    elif fallito:
        parti.append(f"FALLITI: {fallito.group(1)}")
    return " · ".join(parti) or "esito non riconosciuto (nessuna riga 'Ran' nella suite)"


def format_thread(filo: dict, mostra_detail: bool = True) -> str:
    """Il filo come si legge in un terminale: una riga per messaggio, in ordine."""
    testa = (f"filo {filo.get('trace', '?')}  ·  "
             + (" → ".join(filo.get("participants") or []) or "nessun mittente")
             + f"  ·  {len(filo.get('messages') or [])} messaggi  ·  esito: "
             + (filo.get("verdict") or "aperto"))
    righe = [testa]
    for m in filo.get("messages") or []:
        tipo = log_field(m, "type")
        chi = log_field(m, "sourceNode", "source") or "?"
        verso = log_field(m, "targetNode", "target")
        ts = log_field(m, "ts", "timestamp").replace("T", " ").replace("Z", "")
        righe.append(f"  [{ts}] {TIPI_LABEL.get(tipo, tipo):9} {chi}"
                     f"{' → ' + verso if verso else ''}  ({log_field(m, 'status')})")
        righe.append(f"      {log_field(m, 'summary')}")
        dettaglio = log_field(m, "detail")
        if mostra_detail and dettaglio:
            tagliato = dettaglio if len(dettaglio) <= 400 else dettaglio[:400] + "…"
            righe.append(f"      ↳ {tagliato}")
    return "\n".join(righe)


# ── I/O ───────────────────────────────────────────────────────────────────────
def _richiesta(cp: str, percorso: str, corpo=None, timeout: int = 15) -> dict:
    """Una richiesta JSON al control-plane. Solo urllib (gira anche su Windows)."""
    dati = json.dumps(corpo).encode() if corpo is not None else None
    intestazioni = {"Content-Type": "application/json"} if dati else {}
    richiesta = urllib.request.Request(f"{cp}{percorso}", data=dati, headers=intestazioni)
    with urllib.request.urlopen(richiesta, timeout=timeout) as risposta:
        return json.loads(risposta.read().decode() or "{}")


def pubblica(cp: str, corpo: dict) -> dict:
    return _richiesta(cp, "/logs/add", corpo)


def leggi_per_tipo(cp: str, tipo: str, per_page: int = 100) -> list:
    dati = _richiesta(cp, f"/logs?type={tipo}&per_page={per_page}&page=1")
    righe = dati.get("logs")
    return righe if isinstance(righe, list) else []


def leggi_tutti_i_messaggi(cp: str, per_page: int = 100) -> list:
    """I tre tipi uniti: e' quello che fa anche il bridge per la dashboard."""
    righe: list = []
    for tipo in TIPI:
        righe += leggi_per_tipo(cp, tipo, per_page=per_page)
    return righe


def esegui_suite(comando) -> tuple:
    """Esegue la suite vera e restituisce `(exit_code, output)`."""
    esito = subprocess.run(list(comando), capture_output=True, text=True)
    return esito.returncode, (esito.stdout or "") + (esito.stderr or "")


# ── CLI ───────────────────────────────────────────────────────────────────────
def nuovo_trace() -> str:
    return uuid.uuid4().hex[:8]


def _cmd_propose(args) -> int:
    trace = args.trace or nuovo_trace()
    dettaglio = args.detail or (f"forge://artifact/{args.artifact}" if args.artifact else "")
    corpo = messaggio("code_proposal", trace, args.source, args.to, args.summary, dettaglio)
    if args.dry:
        print(json.dumps(corpo, ensure_ascii=False, indent=2))
        return 0
    pubblica(args.cp, corpo)
    print(f"proposta pubblicata sul filo {trace}")
    if not args.trace:
        print(f"  per revisione e verdetto usa --trace {trace}: senza, ognuno aprirebbe un filo a se'")
    return 0


def _cmd_review(args) -> int:
    corpo = messaggio("code_review", args.trace, args.source, args.to, args.summary,
                      args.detail, args.status)
    if args.dry:
        print(json.dumps(corpo, ensure_ascii=False, indent=2))
        return 0
    pubblica(args.cp, corpo)
    print(f"revisione pubblicata sul filo {args.trace}")
    return 0


def _cmd_verdict(args) -> int:
    corpo = messaggio("code_verdict", args.trace, args.source, args.to, args.summary,
                      args.detail, args.status)
    if args.dry:
        print(json.dumps(corpo, ensure_ascii=False, indent=2))
        return 0
    pubblica(args.cp, corpo)
    print(f"verdetto pubblicato: {args.status}")
    return 0


def _cmd_gate(args) -> int:
    # `args.cmd` e' "" quando non lo passi: `"".split()` darebbe un comando VUOTO,
    # e subprocess fallirebbe con un traceback invece di eseguire la suite.
    comando = args.cmd.split() if str(args.cmd).strip() else list(SUITE_DEFAULT)
    print(f"gate: eseguo {' '.join(comando)}")
    codice, uscita = esegui_suite(comando)
    riassunto = summarize_suite(uscita)
    stato = args.status or ("success" if codice == 0 else "failed")
    coda = "\n".join(uscita.strip().splitlines()[-max(1, args.tail):])
    dettaglio = args.detail or f"{riassunto} (exit {codice})\n--- coda della suite ---\n{coda}"
    corpo = messaggio("code_verdict", args.trace, args.source, args.to,
                      args.summary or f"gate automatico: {riassunto}", dettaglio, stato)
    if args.dry:
        print(json.dumps(corpo, ensure_ascii=False, indent=2))
        return 0
    pubblica(args.cp, corpo)
    print(f"verdetto pubblicato: {stato} · {riassunto}")
    return 0 if codice == 0 else 1


def _cmd_thread(args) -> int:
    fili = group_threads(leggi_tutti_i_messaggi(args.cp, per_page=args.limit))
    filo = next((f for f in fili if f["trace"] == args.trace), None)
    if not filo:
        print(f"nessun messaggio sul filo {args.trace}: trace sbagliato, o filo mai aperto")
        return 1
    print(format_thread(filo, mostra_detail=not args.brief))
    return 0


def _cmd_threads(args) -> int:
    fili = group_threads(leggi_tutti_i_messaggi(args.cp, per_page=args.limit))
    if not fili:
        print("nessuna conversazione. Il canale e' vivo ma vuoto: vedi docs/code-conversation.md")
        return 0
    for filo in fili:
        ultimo = filo["messages"][-1]
        quando = log_field(ultimo, "ts", "timestamp").replace("T", " ").replace("Z", "")
        print(f"  {filo['trace']}  {quando}  {' → '.join(filo['participants']) or '—'}"
              f"  {len(filo['messages'])} msg  {filo['verdict'] or 'aperto'}")
        print(f"        {log_field(filo['messages'][0], 'summary')[:88]}")
    return 0


def costruisci_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="code_channel",
        description="Canale di conversazione fra agenti che scrivono codice (docs/code-conversation.md)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cp", default=CP_DEFAULT, help=f"control-plane (default: {CP_DEFAULT})")
    sub = p.add_subparsers(dest="comando", required=True)

    prop = sub.add_parser("propose", help="apre un filo con una proposta (il filo lo crea se manca)")
    prop.add_argument("--summary", required=True, help="intento in una riga")
    prop.add_argument("--to", default="", help="revisore (alias del nodo)")
    prop.add_argument("--trace", default="", help="filo esistente; se manca ne crea uno e lo stampa")
    prop.add_argument("--from", dest="source", default=nodo_locale())
    prop.add_argument("--artifact", default="", help="id dell'artefatto Forge: diventa il detail")
    prop.add_argument("--detail", default="")
    prop.add_argument("--dry", action="store_true", help="stampa il messaggio senza pubblicarlo")
    prop.set_defaults(funzione=_cmd_propose)

    rev = sub.add_parser("review", help="aggiunge una revisione al filo")
    rev.add_argument("--trace", required=True)
    rev.add_argument("--summary", required=True)
    rev.add_argument("--from", dest="source", default=nodo_locale())
    rev.add_argument("--to", default="")
    rev.add_argument("--status", default="info", choices=["info", "warning"])
    rev.add_argument("--detail", default="")
    rev.add_argument("--dry", action="store_true")
    rev.set_defaults(funzione=_cmd_review)

    ver = sub.add_parser("verdict", help="chiude il filo con un esito dichiarato")
    ver.add_argument("--trace", required=True)
    ver.add_argument("--summary", required=True)
    ver.add_argument("--status", default="success", choices=["success", "failed"])
    ver.add_argument("--from", dest="source", default=nodo_locale())
    ver.add_argument("--to", default="")
    ver.add_argument("--detail", default="")
    ver.add_argument("--dry", action="store_true")
    ver.set_defaults(funzione=_cmd_verdict)

    gate = sub.add_parser("gate", help="esegue la suite e pubblica il verdetto REALE")
    gate.add_argument("--trace", required=True)
    gate.add_argument("--from", dest="source", default=nodo_locale())
    gate.add_argument("--to", default="")
    gate.add_argument("--cmd", default="", help="comando della suite (default: "
                                                + " ".join(SUITE_DEFAULT) + ")")
    gate.add_argument("--status", default="", choices=["", "success", "failed"],
                      help="forza l'esito; di default lo decide l'exit code della suite")
    gate.add_argument("--summary", default="")
    gate.add_argument("--detail", default="")
    gate.add_argument("--tail", type=int, default=12, help="righe di coda nel detail")
    gate.add_argument("--dry", action="store_true")
    gate.set_defaults(funzione=_cmd_gate)

    th = sub.add_parser("thread", help="stampa un filo in ordine")
    th.add_argument("--trace", required=True)
    th.add_argument("--limit", type=int, default=100, help="righe lette per tipo")
    th.add_argument("--brief", action="store_true", help="senza i dettagli")
    th.set_defaults(funzione=_cmd_thread)

    ths = sub.add_parser("threads", help="elenca i fili, piu' recenti prima")
    ths.add_argument("--limit", type=int, default=100)
    ths.set_defaults(funzione=_cmd_threads)
    return p


def main(argv=None) -> int:
    args = costruisci_parser().parse_args(argv)
    try:
        return args.funzione(args)
    except ValueError as errore:
        # Vocabolario sbagliato (tipo fuori elenco, summary o trace mancanti): e'
        # colpa del chiamante, e va detto prima di spedire, non dopo.
        print(f"errore: {errore}", file=sys.stderr)
        return 2
    except urllib.error.HTTPError as errore:
        print(f"il control-plane ha risposto {errore.code}: "
              f"{errore.read().decode(errors='replace')[:200]}", file=sys.stderr)
        return 2
    except urllib.error.URLError as errore:
        print(f"control-plane non raggiungibile su {args.cp}: {errore.reason}", file=sys.stderr)
        print("  controlla --cp, o che il container sia su.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())



