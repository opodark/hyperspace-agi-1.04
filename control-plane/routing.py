# control-plane/routing.py
# Scoring di routing metric-driven (v1.05). Funzioni pure e testabili.
#
# Filosofia (ibrida): la QUALITÀ osservata (latenza/throughput per modello,
# pressione VRAM del motore) domina quando i campioni /metrics sono freschi;
# il blocco STRUTTURALE (vram/tier/uptime/backend) è il fallback quando i
# dati mancano o sono stale. Un gate di salute (health_s) moltiplica il tutto
# (nodo down/schema non compatibile -> score ~0). Infine un termine
# "recent_s" negativo scoraggia di ripickare lo stesso nodo appena scelto,
# per bilanciare il carico tra nodi di pari valore.
#
# Normalizzazione: i segnali di qualità e la VRAM sono normalizzati rispetto
# al set di candidati (min-max), così lo score cambia con le metriche
# osservate e nessun termine domina per costruzione.
#
# Le funzioni NON toccano stato globale: tutto passa come argomento.

import time

from shared.engine_profiles import backend_type_score as _backend_type_score


def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else (hi if x > hi else x)


def _minmax(values):
    """min/max sui valori non-None; ritorna (min, max, n)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None, None, 0
    return min(vals), max(vals), len(vals)


def _norm_high(x, lo, hi):
    """Normalizza 0..1 (più alto meglio); 1.0 se singolo valore o invariato."""
    if x is None:
        return 0.5
    if hi is None or hi == lo:
        return 1.0
    return _clamp((x - lo) / (hi - lo))


def _norm_low(x, lo, hi):
    """Normalizza invertita (più basso meglio)."""
    return 1.0 - _norm_high(x, lo, hi)


# ── estrazione segnali grezzi (per nodo) ─────────────────────────────────
def extract_signal(node: dict, metrics: dict, model: str = "") -> dict:
    """Fonde nodo heartbeat + ultimo campione /metrics in un segnale grezzo.
    `metrics` può essere None (nodo senza campioni o irraggiungibile)."""
    m_load = (metrics or {}).get("load", {}) if metrics else {}
    has_metrics = bool(metrics)
    is_v3 = has_metrics and m_load.get("saturation") is not None

    # saturazione: v3 dal nodo (o dal sample), v2 fallback a unità.
    saturation = m_load.get("saturation")
    if saturation is None:
        saturation = node.get("saturation")
    degraded = m_load.get("degraded")
    if degraded is None:
        degraded = node.get("degraded", False)

    v2_load_ratio = None
    max_c = max(float(node.get("max_load_units", node.get("max_concurrent", 1)) or 1), 0.01)
    active = float(node.get("active_load", node.get("active_requests", 0)) or 0)
    queued = float(node.get("queued_load", node.get("queued_requests", 0)) or 0)
    v2_load_ratio = (active + queued) / max_c

    server = (metrics or {}).get("server", {}) if metrics else {}
    runtime = (metrics or {}).get("runtime", {}) if metrics else {}

    # Latenza/throughput model-aware: runtime[model] se presente (Ollama),
    # altrimenti segnali server-wide (vLLM: p50 + tokens_per_sec).
    lat = tps = None
    if model and model in runtime:
        e = runtime[model]
        lat = e.get("latency_ms_ewma")
        tps = e.get("tokens_per_sec_ewma")
    if lat is None:
        lat = server.get("latency_ms_p50", server.get("latency_ms_p95"))
    if tps is None:
        tps = server.get("tokens_per_sec")

    health_up = server.get("health", {}).get("up") if has_metrics else None
    age = None
    if has_metrics and metrics.get("collected_at") is not None:
        try:
            from datetime import datetime, timezone
            t = datetime.fromisoformat(str(metrics["collected_at"]))
            age = max((datetime.now(timezone.utc) - t).total_seconds(), 0.0)
        except Exception:
            age = None

    return {
        "node_id":       node.get("node_id", ""),
        "vram_gb":       float(node.get("vram_gb", 0) or 0),
        "tier":          node.get("tier", "leaf"),
        "uptime_s":      int(node.get("uptime_s", 0) or 0),
        "backend_type":  node.get("backend_type") or (node.get("capability_profile") or {}).get("backend_type") or "model_manager",
        "active":        (float(node.get("active_requests", 0)) > 0)
                         or (float(m_load.get("active_requests", 0)) > 0),
        "saturation":    float(saturation) if saturation is not None else None,
        "degraded":      bool(degraded),
        "v2_load_ratio": v2_load_ratio,
        "lat":           float(lat) if lat is not None else None,
        "tps":           float(tps) if tps is not None else None,
        "gpu_usage":     float(server.get("gpu_cache_usage_perc")) if has_metrics and server.get("gpu_cache_usage_perc") is not None else None,
        "has_metrics":   has_metrics,
        "is_v3":         is_v3,
        "health_up":     health_up,
        "schema_mismatch": bool((metrics or {}).get("schema_mismatch", False)),
        "metrics_age_s": age,
    }


# ── normalizzazione rispetto al set di candidati ──────────────────────────
def rank_signals(sigs: list, weights: dict, metrics_interval_s: float = 20.0):
    """Riempie in place i campi normalizzati di ogni segnale. `weights` serve
    solo per i denominatori dei blocchi. Ritorna la lista (stesso oggetto)."""
    lat_lo, lat_hi, _ = _minmax([s["lat"] for s in sigs])
    tps_lo, tps_hi, _ = _minmax([s["tps"] for s in sigs])
    vram_lo, vram_hi, _ = _minmax([s["vram_gb"] for s in sigs])

    for s in sigs:
        s["lat_s"] = _norm_low(s["lat"], lat_lo, lat_hi)
        s["tps_s"] = _norm_high(s["tps"], tps_lo, tps_hi)
        s["vram_s"] = _norm_high(s["vram_gb"], vram_lo, vram_hi)
        s["gpu_s"] = _clamp(1.0 - (s["gpu_usage"] or 0) / 100.0) if s["gpu_usage"] is not None else 0.5
        s["tier_s"] = {"root": 3, "hub": 2, "leaf": 1}.get(s["tier"], 1) / 3.0
        s["uptime_s"] = min(s["uptime_s"], 604800) / 604800.0
        s["backend_s"] = _backend_type_score(s["backend_type"])

        # saturazione -> load_s
        if s["saturation"] is not None:
            s["load_s"] = 0.0 if s["degraded"] else _clamp(1.0 - s["saturation"])
        elif s["v2_load_ratio"] is not None:
            s["load_s"] = _clamp(1.0 - min(s["v2_load_ratio"], 1.5))
        else:
            s["load_s"] = 0.5

        # disponibilità qualità (fresca)
        fresh = s["metrics_age_s"] is None or s["metrics_age_s"] <= 3.0 * metrics_interval_s
        avail = [k for k in ("lat", "tps", "gpu_usage")
                 if s[k] is not None and fresh]
        s["q"] = len(avail) / 3.0
        s["_avail"] = avail

        # salute: 0 se il nodo riporta health down; 0.4 se il campione è
        # stale; 1 se up e fresco; 0.6 neutro (niente campioni o formato
        # legacy — il fallback strutturale/load v2 resta comunque leggibile).
        # Un nodo che si dichiara DEGRADATO (rifiuta nuovo lavoro) viene
        # depenalizzato a 0.4: il routing lo evita e gli lascia recuperare.
        if s["has_metrics"] and s["health_up"] is False:
            s["health_s"] = 0.0
        elif s["has_metrics"] and not fresh:
            s["health_s"] = 0.4
        elif s["has_metrics"] and s["health_up"] is True:
            s["health_s"] = 1.0
        else:
            s["health_s"] = 0.6
        if s["degraded"]:
            s["health_s"] = min(s["health_s"], 0.4)
    return sigs


def _quality_score(s: dict, weights: dict) -> float:
    w = {"latency": float(weights.get("latency", 0.45)),
         "tput": float(weights.get("tput", 0.35)),
         "gpu": float(weights.get("gpu", 0.20))}
    parts = []
    if s["lat"] is not None:
        parts.append((w["latency"], s["lat_s"]))
    if s["tps"] is not None:
        parts.append((w["tput"], s["tps_s"]))
    if s["gpu_usage"] is not None:
        parts.append((w["gpu"], s["gpu_s"]))
    if not parts:
        return 0.5, []
    wsum = sum(p[0] for p in parts)
    score = sum(p[0] * p[1] for p in parts) / wsum
    terms = [("lat_s", w["latency"] / wsum * s["lat_s"]),
             ("tps_s", w["tput"] / wsum * s["tps_s"]),
             ("gpu_s", w["gpu"] / wsum * s["gpu_s"])]
    keep = s.get("_avail", [])
    return score, [(k, v) for k, v in terms
                   if (k == "lat_s" and "lat" in keep)
                   or (k == "tps_s" and "tps" in keep)
                   or (k == "gpu_s" and "gpu_usage" in keep)]


def _structural_score(s: dict, weights: dict) -> tuple:
    w = {"vram": float(weights.get("vram", 0.55)),
         "load": float(weights.get("load", 0.25)),
         "tier": float(weights.get("tier", 0.10)),
         "uptime": float(weights.get("uptime", 0.10)),
         "engine": float(weights.get("engine", 0.15))}
    wsum = sum(w.values()) or 1.0
    terms = [("vram_s", w["vram"] / wsum * s["vram_s"]),
             ("load_s", w["load"] / wsum * s["load_s"]),
             ("tier_s", w["tier"] / wsum * s["tier_s"]),
             ("uptime_s", w["uptime"] / wsum * s["uptime_s"]),
             ("backend_s", w["engine"] / wsum * s["backend_s"])]
    score = sum(v for _, v in terms)
    return score, terms


def compute_score(sig: dict, weights: dict, recent_ts: float = None, now: float = None) -> tuple:
    """Ritorna (score, breakdown) per un segnale già normalizzato (rank_signals).
    breakdown è un dict di termini PESATI che sommano esattamente a score
    (health_s è esposto come gate informativo, non si somma)."""
    import math
    now = now if now is not None else time.time()

    q = sig["q"]
    quality, qterms = _quality_score(sig, weights)
    structural, sterms = _structural_score(sig, weights)
    blended = q * quality + (1.0 - q) * structural
    gated = sig["health_s"] * blended

    # Penalità "ultimo scelto": decresce esponenzialmente con la finestra
    # recent_window. Piccola (default 0.10) -> rompe i pareggi senza far
    # perdere un nodo nettamente migliore.
    recent_penalty = 0.0
    if recent_ts:
        dt = max(now - recent_ts, 0.0)
        recent_penalty = float(weights.get("recent_penalty", 0.10)) * math.exp(
            -dt / float(weights.get("recent_window", 45.0)))

    score = gated - recent_penalty

    breakdown = {}
    for key, val in sterms:
        breakdown[key] = round(sig["health_s"] * (1.0 - q) * val, 4)
    for key, val in qterms:
        breakdown[key] = round(sig["health_s"] * q * val, 4)
    breakdown["recent_s"] = round(-recent_penalty, 4)
    breakdown["health_s"] = round(sig["health_s"], 3)
    breakdown["q"] = round(q, 3)
    return score, breakdown
