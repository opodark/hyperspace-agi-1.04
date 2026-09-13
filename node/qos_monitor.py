# node/qos_monitor.py
# QoS monitor — percezione delle prestazioni per il limiter adattativo.
#
# Sostituisce la stima di "unità di carico"/capacità (MAX_LOAD_UNITS) con una
# misura OSSERVATA di quanto il nodo sta degradando rispetto a un riferimento
# auto-calibrato:
#   ref_lat : latenza di base del modello (tracciamento lento del minimo, con
#             deriva verso l'alto se il regime cambia davvero)
#   ref_tok : throughput di riferimento (tracciamento lento del picco, con
#             decadimento lentissimo sotto carico sostenuto)
#
# La degradazione è in PERCENTUALE rispetto a questi riferimenti:
#   lat_pct = (lat/ref_lat - 1)*100   -> cresce con la coda/contesa
#   tok_pct = (1 - tok/ref_tok)*100   -> cresce quando il throughput crolla
#
# Aggregata sui modelli ATTIVI (active_by_model) per il backend model_manager,
# sui segnali server-wide (p50/tokens_per_sec) per inference_server.
#
# Nessuna dipendenza da config fragili: i riferimenti si auto-calibrano dai
# dati osservati; le soglie di intervento sono solo i "valori standard".


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else (hi if x > hi else x)


class QoSMonitor:
    def __init__(self, backend_type: str, degraded_pct: float = 100.0,
                 tps_collapse_pct: float = 40.0):
        self.backend_type = backend_type
        self.degraded_pct = max(degraded_pct, 1.0)
        self.tps_collapse_pct = _clamp(tps_collapse_pct, 1.0, 99.0)
        # unit -> {"lat": float|None, "tok": float|None}
        self._ref: dict = {}
        self.degradation_pct = 0.0
        self.degraded = False
        self.saturation = 0.0
        self.calibrated = False

    # ── calibrazione riferimenti ────────────────────────────
    def _update_unit(self, unit: str, lat, tok):
        ref = self._ref.setdefault(unit, {"lat": None, "tok": None})
        if lat is not None and lat > 0:
            cur = ref["lat"]
            if cur is None:
                ref["lat"] = float(lat)
            elif lat < cur:
                ref["lat"] = 0.9 * cur + 0.1 * float(lat)      # insegue i nuovi minimi
            else:
                ref["lat"] = cur + 0.02 * (float(lat) - cur)   # deriva lenta verso l'alto
        if tok is not None and tok > 0:
            cur = ref["tok"]
            if cur is None:
                ref["tok"] = float(tok)
            elif tok > cur:
                ref["tok"] = 0.7 * cur + 0.3 * float(tok)      # insegue i nuovi picchi
            else:
                ref["tok"] = cur * 0.998                        # decadimento lentissimo

    def _degradation(self, unit: str, lat, tok):
        ref = self._ref.get(unit)
        if not ref:
            return 0.0
        lat_pct = 0.0
        if lat is not None and ref.get("lat"):
            lat_pct = max((float(lat) / ref["lat"] - 1.0) * 100.0, 0.0)
        tok_pct = 0.0
        if tok is not None and ref.get("tok"):
            tok_pct = max((1.0 - float(tok) / ref["tok"]) * 100.0, 0.0)
        return max(lat_pct, tok_pct)

    # ── aggiornamento ───────────────────────────────────────
    def update(self, server: dict, runtime: dict, active_by_model: dict):
        """Aggiorna i riferimenti e ricalcola degradazione/saturazione.
        'active_by_model': modello -> conteggio richieste in-flight."""
        active_by_model = active_by_model or {}
        if self.backend_type == "inference_server":
            # Segnali server-wide (vLLM): p50/p95 e throughput aggregati.
            lat = server.get("latency_ms_p50", server.get("latency_ms_p95"))
            tok = server.get("tokens_per_sec")
            self._update_unit("__server__", lat, tok)
            self.calibrated = self._ref.get("__server__", {}).get("lat") is not None \
                or self._ref.get("__server__", {}).get("tok") is not None
            active = bool([c for c in active_by_model.values() if c and float(c) > 0])
            self._aggregate(("__server__",), lat, tok, active)
        else:
            for name, e in (runtime or {}).items():
                self._update_unit(name, e.get("latency_ms_ewma"), e.get("tokens_per_sec_ewma"))
            self.calibrated = any(
                ref.get("lat") is not None or ref.get("tok") is not None
                for ref in self._ref.values()
            )
            active_models = [m for m, c in active_by_model.items()
                             if c and float(c) > 0 and m in (runtime or {})]
            if active_models:
                worst = 0.0
                for m in active_models:
                    e = runtime.get(m, {})
                    worst = max(worst, self._degradation(
                        m, e.get("latency_ms_ewma"), e.get("tokens_per_sec_ewma")))
                self._aggregate(active_models, None, None, True, worst=worst)
            else:
                self._aggregate([], None, None, False)

    def _aggregate(self, units, lat, tok, active: bool, worst: float = None):
        if not active:
            self.degradation_pct = 0.0
            self.degraded = False
            self.saturation = 0.0
            return
        if worst is None:
            worst = self._degradation(units[0], lat, tok) if units else 0.0
        self.degradation_pct = round(worst, 1)
        lat_trigger = self.degraded_pct
        tok_trigger = 100.0 - self.tps_collapse_pct
        self.degraded = worst >= lat_trigger or worst >= tok_trigger
        sat_denom = min(lat_trigger, tok_trigger) or 1.0
        self.saturation = round(_clamp(worst / sat_denom), 3)

    def refs(self) -> dict:
        return {k: dict(v) for k, v in self._ref.items()}
