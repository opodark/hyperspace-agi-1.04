# node/backend_metrics.py
# HyperSpace AGI — Prototipo: metriche backend normalizzate
#
# Il nodo è un semplice EXPORTER: espone capability statiche + stato runtime
# osservato dal motore d'inferenza in uno schema normalizzato consumabile dal
# control-plane. La decisione di routing resta al control-plane (che aggrega,
# confronta e decide): questo modulo non prende decisioni.
#
# Il point è NON duplicare pesi "a mano" lato CP (ENGINE_SCORES): ogni backend
# dichiara cosa sa fare (capability_profile) e come si comporta adesso
# (runtime/server). Il control-plane confronta telemetria osservata, non
# punteggi hardcoded per prodotto.
#
# Schema della risposta (GET /metrics sul nodo):
#   node_id / engine / backend_type / capability_profile / load / sampled_at
#   schema_version : versione dello schema (v2), per gestire deployment eterogenei
#   server  : metriche aggregate a livello di backend (code, util, throughput,
#             mean/p50/p95 per TTFT e latenza, health)
#   runtime : metriche PER MODELLO (loaded, vram, EWMA tok/s e latenza,
#             campioni visti, data_age_s)
#   load.active_by_model : modello -> unità di carico attive ADESSO (quale
#             modello sta eseguendo le richieste correnti). Aggiunto in v2
#             senza bump: i consumer più datati lo ignorano, i nodi più datati
#             semplicemente non lo espongono.
#
# Affidabilità del dato (come fidarsi):
#   server.health.up        -> il backend è raggiungibile (false = giù, non
#                              "senza dati")
#   runtime.<m>.data_age_s  -> età dell'ultima misurazione del modello (valori
#                              vecchi restano visibili ma depotenziate dal
#                              decay EWMA; il consumatore vede la vetustà)
#
# sample_source (runtime.<model>) / tokens_per_sec_source (server) chiarisce
# COME è stato ottenuto il tok/s, così il consumatore sa quanto fidarsi:
#   "ewma"            -> smussato su più richieste osservate (Ollama/memory)
#   "gauge"           -> gauge unlabeled letta dal backend (vLLM)
#   "gauge_aggregate" -> somma di gauge per-modello (vLLM con label model_name)
#   "counter_rate"    -> delta di un counter / delta di tempo (vLLM vecchie)
#   None              -> nessun campione: il valore non c'è

import asyncio
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ── CONFIG ───────────────────────────────────────────────
DATA_DIR = os.getenv("DATA_DIR", "/app/data")
MEMORY_FILE = Path(DATA_DIR) / "memory.jsonl"

# Cache della risposta: il control-plane fa pull periodico, ma senza questa
# cache ogni poll ricontatterebbe il backend (Ollama /api/ps, vLLM /metrics)
# anche per payload identici al secondo precedente.
METRICS_CACHE_TTL_S = float(os.getenv("METRICS_CACHE_TTL_S", "8"))
# EWMA tok/s e latenza per modello. Finestra PER MODELLO (non globale): ogni
# modello tiene le ultime METRICS_EWMA_MODEL_WINDOW interazioni osservate,
# così un modello molto usato non "ruba" la finestra a quelli usati raramente.
# I campioni vengono letti dalle ultime METRICS_EWMA_READ_MAX righe di
# memory.jsonl (limite di I/O; METRICS_EWMA_WINDOW resta come alias
# retrocompatibile di READ_MAX).
# Il DECAY temporale pesa i campioni per età (0.5^(age/half_life)): i dati
# recenti contano di più, ma quelli vecchi NON vengono scartati (restano
# visibili, depotenziati) — l'indicazione di una metrica rimane anche quando
# mancano misurazioni fresche, e data_age_s per modello ne dichiara la vetustà.
METRICS_EWMA_READ_MAX      = int(os.getenv("METRICS_EWMA_READ_MAX", os.getenv("METRICS_EWMA_WINDOW", "2000")))
METRICS_EWMA_MODEL_WINDOW  = int(os.getenv("METRICS_EWMA_MODEL_WINDOW", "50"))
METRICS_EWMA_HALF_LIFE_S   = float(os.getenv("METRICS_EWMA_HALF_LIFE_S", "1800"))
METRICS_EWMA_ALPHA         = float(os.getenv("METRICS_EWMA_ALPHA", "0.25"))

VLLM_METRICS_URL = os.getenv("VLLM_METRICS_URL", "").strip().rstrip("/")
VLLM_URL         = os.getenv("VLLM_URL", "").strip().rstrip("/")
VLLM_MODELS      = [m.strip() for m in os.getenv("VLLM_MODELS", "").split(",") if m.strip()]

# ── CAPABILITY (statiche: distinguono il PARADIGMA, non il prodotto) ────────
#   inference_server  -> modello persistente, batching continuo, alta
#                        concorrenza, latenza prevedibile (vLLM, TGI, SGLang)
#   model_manager     -> gestione dinamica dei modelli, load/unload on-demand,
#                        grande flessibilità, concorrenza nativa minore
#                        (Ollama, LM Studio)
_CAPABILITY_PROFILES = {
    "vllm": {
        "backend_type":        "inference_server",
        "continuous_batching": True,
        "paged_attention":     True,
        "dynamic_loading":     False,
        "model_persistence":   True,
        "streaming":           True,
    },
    "ollama": {
        "backend_type":        "model_manager",
        "continuous_batching": False,
        "paged_attention":     False,
        "dynamic_loading":     True,
        "model_persistence":   False,
        "streaming":           True,
    },
}

def capability_profile(engine: str) -> dict:
    """Profilo di capability del motore. 'ollama' resta il default: qualunque
    motore non riconosciuto come inference_server viene trattato come
    model_manager (conservativo: capacità di concorrenza incerta)."""
    return _CAPABILITY_PROFILES.get(engine, _CAPABILITY_PROFILES["ollama"])

# ── HELPERS ──────────────────────────────────────────────
def _read_memory(limit: int) -> list:
    """Ultime `limit` interazioni dalla memoria collettiva locale
    (data/memory.jsonl), scritta dal proxy (ollama_proxy.py) e condivisa con
    il node agent — stessi processi, stesso DATA_DIR."""
    if not MEMORY_FILE.exists():
        return []
    try:
        lines = MEMORY_FILE.read_text(encoding="utf-8").strip().splitlines()
    except Exception:
        return []
    return [json.loads(l) for l in lines[-limit:] if l.strip()]


def _ewma(values: list, alpha: float = METRICS_EWMA_ALPHA) -> float:
    """Media mobile esponenziale (in ordine di arrivo). None se non ci sono
    campioni: il consumatore distingue 'nessun dato' da 'dato = 0'."""
    e = None
    for v in values:
        if v is None:
            continue
        e = v if e is None else alpha * v + (1.0 - alpha) * e
    return round(e, 2) if e is not None else None


def _parse_ts(ts) -> float:
    """Timestamp di una interazione -> epoch float. Accetta ISO (con o senza
    'Z', con o senza offset) o epoch numerico. Se non parsabile -> None."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    if not isinstance(ts, str):
        return None
    try:
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _ewma_decayed(samples: list, alpha: float, half_life_s: float, now: float) -> float:
    """Media pesata con decay temporale per età, in ordine di arrivo.
    Ogni campione (value, ts) pesa w = 0.5^(age/half_life): i dati recenti
    contano di più, quelli vecchi contribuiscono meno ma NON vengono scartati
    (l'indicazione resta presente anche senza misurazioni fresche). Campioni
    con ts non parsabile sono trattati come se fossero freschi (age=0)."""
    e = None
    total_w = 0.0
    for v, ts in samples:
        if v is None:
            continue
        ts_e = _parse_ts(ts)
        age = max(now - ts_e, 0.0) if ts_e is not None else 0.0
        w = 0.5 ** (age / max(half_life_s, 1.0))
        if w <= 0:
            continue
        if e is None:
            e, total_w = v, w
        else:
            e = (e * total_w + v * w) / (total_w + w)
            total_w += w
    return round(e, 2) if e is not None else None


def _bytes_to_gb(value) -> float:
    try:
        b = float(value or 0)
        if b <= 0:
            return None
        return round(b / 1e9, 2)
    except Exception:
        return None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Stato per il rate derivato dai counter Prometheus (vLLM generation_tokens):
# il tok/s si ottiene come delta del counter su delta di tempo tra due poll.
_token_rate_state: dict = {}

def _counter_rate(key: tuple, value: float, now: float) -> float:
    """Rate (eventi/s) da un counter Prometheus. -1 se il primo campione o se
    il counter è stato resettato (impossibile derivare un rate affidabile)."""
    prev = _token_rate_state.get(key)
    _token_rate_state[key] = {"v": value, "at": now}
    if not prev or now <= prev["at"]:
        return -1.0
    dt = now - prev["at"]
    dv = value - prev["v"]
    if dt <= 0 or dv < 0:
        return -1.0
    return dv / dt

# ── PROVIDER: OLLAMA ─────────────────────────────────────
class OllamaMetricsProvider:
    """Legge stato e telemetria da un Ollama nativo (OLLAMA_URL).
    - /api/ps    : modelli caricati + VRAM occupata (snapshot live)
    - /api/tags  : tutti i modelli disponibili
    - memory.jsonl : EWMA per modello di tok/s e latenza (dati osservati)"""

    def __init__(self, ollama_url: str):
        self.base = ollama_url.rstrip("/")

    async def collect(self) -> dict:
        (ps, ps_err), (tags, tags_err) = await asyncio.gather(self._ps(), self._tags())

        loaded = {m.get("name"): m for m in ps if m.get("name")}

        # EWMA per modello dai log della memoria condivisa.
        # ATTENZIONE: memory.jsonl contiene anche entry PROPAGATE dagli altri
        # nodi via /memory/push (memory sync inter-nodo). Quelle portano sempre
        # il marker _received_from, le entry locali mai: senza questo filtro la
        # telemetria del nodo risulterebbe inquinata dai campioni dei peer.
        # Finestra PER MODELLO: ogni modello tiene le ultime
        # METRICS_EWMA_MODEL_WINDOW interazioni (un modello molto usato non
        # ruba la finestra agli altri). Il peso di ogni campione decade con
        # l'età (METRICS_EWMA_HALF_LIFE_S) ma i vecchi non vengono scartati:
        # data_age_s dichiara la vetustà del dato più recente.
        agg = {}
        for e in _read_memory(METRICS_EWMA_READ_MAX):
            if e.get("_received_from"):
                continue
            m = e.get("model")
            if not m:
                continue
            d = agg.setdefault(m, {"tps": [], "lat": [], "count": 0, "last_ts": ""})
            tps = e.get("tokens_per_sec")
            if not tps and e.get("tokens_out") and e.get("duration_ms"):
                tps = round(e["tokens_out"] / max(e["duration_ms"] / 1000.0, 0.001), 2)
            if tps:
                d["tps"].append((float(tps), e.get("ts")))
            if e.get("duration_ms"):
                d["lat"].append((float(e["duration_ms"]), e.get("ts")))
            # requests_seen è il numero di campioni EFFETTIVAMENTE usati dalla
            # stima (cappato alla finestra per-modello), coerente con l'EWMA:
            # non il totale di interazioni viste nel file.
            d["count"] = min(d["count"] + 1, METRICS_EWMA_MODEL_WINDOW)
            d["last_ts"] = e.get("ts") or d["last_ts"]
            d["tps"] = d["tps"][-METRICS_EWMA_MODEL_WINDOW:]
            d["lat"] = d["lat"][-METRICS_EWMA_MODEL_WINDOW:]

        now = time.time()
        runtime = {}
        for name, d in agg.items():
            entry = loaded.get(name, {})
            last_ts_e = _parse_ts(d["last_ts"])
            runtime[name] = {
                "loaded":              name in loaded,
                "vram_gb":             _bytes_to_gb(entry.get("size_vram")),
                "tokens_per_sec_ewma": _ewma_decayed(d["tps"], METRICS_EWMA_ALPHA, METRICS_EWMA_HALF_LIFE_S, now),
                "latency_ms_ewma":     _ewma_decayed(d["lat"], METRICS_EWMA_ALPHA, METRICS_EWMA_HALF_LIFE_S, now),
                "requests_seen":       d["count"],
                "last_sample_ts":      d["last_ts"] or None,
                "data_age_s":          round(max(now - last_ts_e, 0.0), 1) if last_ts_e is not None else None,
                "sample_source":       "ewma" if d["count"] > 0 else None,
            }
        # Caricati ma mai visti nei log: stato dal vivo, telemetria assente.
        for name in sorted(loaded):
            if name not in runtime:
                runtime[name] = {
                    "loaded":              True,
                    "vram_gb":             _bytes_to_gb(loaded[name].get("size_vram")),
                    "tokens_per_sec_ewma": None,
                    "latency_ms_ewma":     None,
                    "requests_seen":       0,
                    "last_sample_ts":      None,
                    "data_age_s":          None,
                    "sample_source":       None,
                }

        # Health: distingue "backend raggiungibile" da "giù o senza dati".
        # up = almeno un endpoint risponde (ps e tags sono sullo stesso server).
        server = {
            "models_available": sorted({m.get("name") for m in tags if m.get("name")}),
            "models_loaded":    sorted(loaded.keys()),
            "health": {
                "up":          (ps_err is None) or (tags_err is None),
                "last_error":  ps_err or tags_err,
                "ps_ok":       ps_err is None,
                "tags_ok":     tags_err is None,
                "checked_at":  _iso_now(),
            },
        }
        return {"server": server, "runtime": runtime}

    async def _ps(self) -> tuple:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(f"{self.base}/api/ps")
                if r.status_code == 200:
                    return r.json().get("models", []), None
                return [], f"HTTP {r.status_code}"
        except Exception as e:
            return [], str(e)[:120]

    async def _tags(self) -> tuple:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(f"{self.base}/api/tags")
                if r.status_code == 200:
                    return r.json().get("models", []), None
                return [], f"HTTP {r.status_code}"
        except Exception as e:
            return [], str(e)[:120]

# ── PROVIDER: VLLM ───────────────────────────────────────
class VLLMMetricsProvider:
    """Legge metriche da un server vLLM:
    - endpoint Prometheus (VLLM_METRICS_URL, default http://<host>:9100/metrics)
      con un parser text-format minimale (nessuna dipendenza extra)
    - lista modelli da VLLM_URL /v1/models (best-effort) o da VLLM_MODELS env
    Se il backend è irraggiungibile le metriche runtime restano vuote, ma le
    capability e la struttura ci sono comunque (il CP non deve fallire per
    colpa di un nodo che non risponde alla telemetria)."""

    _GAUGES = {
        "vllm:num_requests_running":          "requests_running",
        "vllm:num_requests_waiting":          "requests_waiting",
        "vllm:num_requests_swapped":          "requests_swapped",
        "vllm:gpu_cache_usage_perc":          "gpu_cache_usage_perc",
        "vllm:avg_generation_throughput_toks_per_s": "tokens_per_sec",
    }

    def __init__(self, metrics_url: str, base_url: str, models: list):
        self.metrics_url = metrics_url
        self.base_url = base_url
        self.models = models

    _SUMMABLE_GAUGES = {
        "vllm:num_requests_running":                  "sum",
        "vllm:num_requests_waiting":                  "sum",
        "vllm:num_requests_swapped":                  "sum",
        "vllm:avg_generation_throughput_toks_per_s":  "sum",
        "vllm:gpu_cache_usage_perc":                  "max",
    }

    async def collect(self) -> dict:
        raw, raw_err = await self._fetch_metrics()
        metrics = self._parse_prom(raw)
        per_model = self._parse_prom_per_model(raw)

        # Server-wide: sample senza label se presente; altrimenti aggrega i
        # sample per-modello (vLLM recenti esportano le gauge con label
        # model_name — somma per i conteggi/throughput, max per l'uso cache).
        server = {}
        for name, key in self._GAUGES.items():
            if name in metrics:
                server[key] = metrics[name]
                if name == "vllm:avg_generation_throughput_toks_per_s":
                    server["tokens_per_sec_source"] = "gauge"
            else:
                pm = per_model.get(name)
                if pm:
                    vals = list(pm.values())
                    server[key] = round(sum(vals), 2) if self._SUMMABLE_GAUGES[name] == "sum" else max(vals)
                    if name == "vllm:avg_generation_throughput_toks_per_s":
                        server["tokens_per_sec_source"] = "gauge_aggregate"

        # Latenza/TTFT: media + p50/p95 dai bucket dell'histogram. Il parser
        # histogram aggrega su TUTTI i set di label (le vLLM esportano a volte
        # _sum/_count con label model_name, che il parser flat scarterebbe
        # facendo sparire la media silenziosamente). La media resta come
        # riferimento; i quantili sono robusti agli outlier.
        for base, pname in (
            ("vllm:time_to_first_token_seconds", "ttft"),
            ("vllm:request_latency_seconds",     "latency"),
        ):
            buckets, bsum, bcount = self._parse_prom_histogram(raw, base)
            if bcount:
                server[f"{pname}_ms_mean"] = round(bsum / bcount * 1000, 1)
            p50 = self._hist_quantile(buckets, bcount, 0.50)
            p95 = self._hist_quantile(buckets, bcount, 0.95)
            if p50 is not None:
                server[f"{pname}_ms_p50"] = round(p50 * 1000, 1)
            if p95 is not None:
                server[f"{pname}_ms_p95"] = round(p95 * 1000, 1)

        # Health: distingue "endpoint Prometheus raggiungibile" da "giù".
        server["health"] = {
            "up":          (raw_err is None) and (raw != ""),
            "last_error":  raw_err,
            "metrics_ok":  (raw_err is None) and (raw != ""),
            "checked_at":  _iso_now(),
        }

        models = list(self.models)
        if not models:
            models = await self._fetch_models()
        server["models_available"] = sorted(models)

        now = time.time()
        tokens_total = per_model.get("vllm:generation_tokens_total", {})
        throughput_per_model = per_model.get("vllm:avg_generation_throughput_toks_per_s", {})

        runtime = {}
        counter_rates = {}
        for m in models:
            entry = {
                "loaded":              True,
                "vram_gb":             None,
                "tokens_per_sec_ewma": None,
                "latency_ms_ewma":     None,
                "requests_seen":       0,
                "last_sample_ts":      None,
                "data_age_s":          None,
                "sample_source":       None,
            }
            # tok/s per modello: gauge dedicata (se label model_name presente),
            # altrimenti rate derivato dal counter generation_tokens_total
            # (disponibile su tutte le versioni vLLM) via delta/delta_t.
            source = None
            tps = throughput_per_model.get(m)
            if tps is not None:
                source = "gauge"
            elif m in tokens_total:
                rate = _counter_rate((self.base_url, m), tokens_total[m], now)
                if rate >= 0:
                    tps = rate
                    source = "counter_rate"
                    counter_rates[m] = rate
            if tps is not None:
                entry["tokens_per_sec_ewma"] = round(float(tps), 2)
                entry["sample_source"] = source
            for gname, fname in (
                ("vllm:num_requests_running", "requests_running"),
                ("vllm:num_requests_waiting", "requests_waiting"),
            ):
                v = per_model.get(gname, {}).get(m)
                if v is not None:
                    entry[fname] = v
            runtime[m] = entry

        # Server-wide senza gauge (vecchie vLLM): somma dei rate dai counter.
        if "tokens_per_sec" not in server and counter_rates:
            server["tokens_per_sec"] = round(sum(counter_rates.values()), 2)
            server["tokens_per_sec_source"] = "counter_rate"

        return {"server": server, "runtime": runtime}

    async def _fetch_metrics(self) -> tuple:
        """Restituisce (text, error). error è None se l'endpoint Prometheus è
        stato letto (HTTP 200 o file leggibile): così il consumatore distingue
        'giù/irraggiungibile' da 'raggiungibile ma senza metriche'."""
        if not self.metrics_url:
            return "", "VLLM_METRICS_URL non configurato"
        path = self.metrics_url
        if path.startswith("file://"):
            path = path[len("file://"):]
        if path.startswith("/") or path.startswith("./") or path.startswith("~"):
            try:
                return Path(os.path.expanduser(path)).read_text(encoding="utf-8", errors="ignore"), None
            except Exception as e:
                return "", str(e)[:120]
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(path)
                if r.status_code == 200:
                    return r.text, None
                return "", f"HTTP {r.status_code}"
        except Exception as e:
            return "", str(e)[:120]

    @staticmethod
    def _parse_prom_histogram(text: str, base_name: str) -> tuple:
        """Aggrega le serie di un histogram Prometheus (`_bucket{le=...}`,
        `_sum`, `_count`) SOMMANDO su tutti i set di label (es. model_name).
        Restituisce (buckets: dict le_float->count, total_sum, total_count)."""
        buckets = {}
        total_sum = 0.0
        total_count = 0.0
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                head, _, rest = line.partition(" ")
                name = head.split("{", 1)[0]
                value = float(rest.split(" ", 1)[0])
            except Exception:
                continue
            if name == f"{base_name}_bucket":
                m = re.search(r'le="([^"]+)"', head)
                le = m.group(1) if m else "+Inf"
                if le == "+Inf":
                    buckets.setdefault("+Inf", 0.0)
                else:
                    try:
                        key = float(le)
                    except ValueError:
                        continue
                    buckets[key] = buckets.get(key, 0.0) + value
            elif name == f"{base_name}_sum":
                total_sum += value
            elif name == f"{base_name}_count":
                total_count += value
        return buckets, total_sum, total_count

    @staticmethod
    def _hist_quantile(buckets: dict, total_count: float, q: float) -> float:
        """Quantile (0..1) da bucket di histogram, con interpolazione lineare
        dentro il bucket che contiene il quantile (stima stile Prometheus).
        None se non ci sono dati."""
        if total_count <= 0:
            return None
        keys = sorted(k for k in buckets if isinstance(k, float))
        if not keys:
            return None
        target = q * total_count
        cum = 0.0
        prev_le = None
        for le in keys:
            prev_cum = cum
            cum += buckets[le]
            if cum >= target:
                if prev_le is None:
                    return le
                frac = (target - prev_cum) / max(buckets[le], 1e-9)
                return prev_le + (le - prev_le) * frac
            prev_le = le
        return keys[-1]

    @staticmethod
    def _parse_prom(text: str) -> dict:
        """Parser text-format Prometheus minimale: per ogni metrica conserva il
        PRIMO campione SENZA label (sufficiente per gauge e per _sum/_count di
        histogram). I sample con label (es. model_name) sono aggregati a parte
        da _parse_prom_per_model, altrimenti il primo di essi verrebbe scambiato
        per un valore server-wide."""
        out = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "{" in line:
                continue
            try:
                head, _, rest = line.partition(" ")
                name = head.split("{", 1)[0]
                value = rest.split(" ", 1)[0]
                out.setdefault(name, float(value))
            except Exception:
                continue
        return out

    @staticmethod
    def _parse_prom_per_model(text: str) -> dict:
        """Sample con label `model_name` raggruppati per metrica e modello.
        Le vLLM moderne esportano gauge/contatori PER MODELLO
        (es. vllm:num_requests_running{model_name="..."}), che il parser flat
        (primo campione) scarterebbe silenziosamente."""
        out = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "{" not in line:
                continue
            try:
                head, _, rest = line.partition(" ")
                name = head.split("{", 1)[0]
                labels = head.split("{", 1)[1].rstrip("}")
                value = rest.split(" ", 1)[0]
                model = None
                for part in labels.split(","):
                    k, _, v = part.partition("=")
                    if k.strip() == "model_name":
                        model = v.strip().strip('"')
                if not model:
                    continue
                out.setdefault(name, {})[model] = float(value)
            except Exception:
                continue
        return out

    @staticmethod
    async def _fetch_models(self) -> list:
        if not self.base_url:
            return []
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(f"{self.base_url}/v1/models")
                if r.status_code != 200:
                    return []
                return [m.get("id") for m in r.json().get("data", []) if m.get("id")]
        except Exception:
            return []

# ── FACTORY + CACHE ──────────────────────────────────────
def get_provider(engine: str):
    """Factory sul motore dichiarato (INFERENCE_BACKEND). vLLM = adapter
    Prometheus; tutto il resto (Ollama, LM Studio, custom) = adapter Ollama:
    è il percorso già strumentato (proxy + memoria condivisa)."""
    if engine == "vllm":
        return VLLMMetricsProvider(VLLM_METRICS_URL, VLLM_URL, VLLM_MODELS)
    return OllamaMetricsProvider(os.getenv("OLLAMA_URL", "http://ollama:11434"))


_cache: dict = {}
_cache_lock = threading.Lock()

def _read_cache(engine: str) -> dict:
    with _cache_lock:
        e = _cache.get(engine)
        if e and time.time() - e["at"] < METRICS_CACHE_TTL_S:
            return e["payload"]
    return None

def _write_cache(engine: str, payload: dict):
    with _cache_lock:
        _cache[engine] = {"at": time.time(), "payload": payload}


# Versione dello schema del payload /metrics. Il control-plane la confronta
# con la propria (NODE_METRICS_SCHEMA_VERSION) per gestire deployment
# eterogenei: un nodo con schema diverso non deve essere interpretato alla
# cieca. Vedi control-plane/main.py.
NODE_METRICS_SCHEMA_VERSION = 2


async def collect_metrics(engine: str = "ollama") -> dict:
    """Payload normalizzato per il control-plane (senza node_id/load, aggiunti
    dalla route in node/main.py). Cache TTL breve per non picchiare il backend
    a ogni poll del CP."""
    cached = _read_cache(engine)
    if cached is not None:
        return cached
    profile = capability_profile(engine)
    try:
        data = await get_provider(engine).collect()
    except Exception:
        data = {"server": {}, "runtime": {}}
    payload = {
        "schema_version":     NODE_METRICS_SCHEMA_VERSION,
        "backend_type":       profile["backend_type"],
        "capability_profile": profile,
        "server":             data.get("server", {}),
        "runtime":            data.get("runtime", {}),
        "sampled_at":         _iso_now(),
    }
    _write_cache(engine, payload)
    return payload
