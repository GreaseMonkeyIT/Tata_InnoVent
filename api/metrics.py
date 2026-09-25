"""VISR verdict as Prometheus series (LOG-088). GET /metrics renders what /api/graph shows at the
moment of the scrape: the root, the findings, the forecast cards, the causal edges, the case match,
the open integrity findings, and the derates in force. Prometheus keeps them next to the plant and
container metrics, so Grafana can put a fault and the verdict about it on one time axis.

Pure functions only: main.py gathers the inputs and this module formats them. The label for a plant
asset is `asset`, not `pod`. The scrape adds its own `pod` label (the api pod), and Prometheus would
rename a second one to `exported_pod`."""

_HELP = {
    "visr_engine_up": "1 when the API could read the engine verdict at this scrape, else 0.",
    "visr_root_active": "Number of root causes in the current verdict.",
    "visr_root_score": "Root cause score of the asset, present only while it is a root.",
    "visr_findings": "Number of open findings in the current verdict.",
    "visr_finding": "1 for each open finding, by asset and class.",
    "visr_forecast_eta_seconds": "Forecast time to the limit, by asset and signal.",
    "visr_forecast_headroom_ratio": "Forecast headroom left before the limit, 0 to 1.",
    "visr_edge_r": "Correlation strength of a causal edge, by source, destination, and signal.",
    "visr_blast_eta_seconds": "Expected time until the impact reaches the asset.",
    "visr_case_match": "1 when the verdict matches a stored case, by register (recurrence or variant).",
    "visr_integrity_open": "Number of open integrity findings, by kind.",
    "visr_derate_pct": "Derate setpoint in force on the asset, percent. Absent at 100.",
}


def _esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def exposition(g, integrity_open=None, derates=None) -> str:
    """Text exposition 0.0.4. `g` is the /api/graph body, or None when the engine did not answer.
    `integrity_open` is the list of open integrity findings. `derates` is fleet.active_derates()."""
    rows: dict[str, list] = {name: [] for name in _HELP}

    def add(name, value, **labels):
        value = _num(value)
        if value is not None:
            rows[name].append((labels, value))

    add("visr_engine_up", 0 if g is None else 1)
    g = g or {}
    roots = g.get("root") or []
    findings = g.get("findings") or []
    if g:
        add("visr_root_active", len(roots))
        add("visr_findings", len(findings))
    for r in roots:
        add("visr_root_score", r.get("score"), asset=r.get("pod"))
    for f in findings:
        add("visr_finding", 1, asset=f.get("pod"), **{"class": f.get("class") or ""})
    for f in g.get("incipient") or []:
        add("visr_forecast_eta_seconds", f.get("eta_s"), asset=f.get("pod"), signal=f.get("signal") or "")
        add("visr_forecast_headroom_ratio", f.get("headroom_frac"), asset=f.get("pod"), signal=f.get("signal") or "")
    for e in g.get("edges") or []:
        add("visr_edge_r", e.get("r"), src=e.get("src"), dst=e.get("dst"), signal=e.get("signal") or "")
    for b in g.get("blast_radius") or []:
        add("visr_blast_eta_seconds", b.get("eta_s"), asset=b.get("pod"))
    reg = (g.get("meta") or {}).get("case_register")
    if reg in ("recurrence", "variant"):
        add("visr_case_match", 1, register=reg)
    if integrity_open is not None:
        kinds: dict[str, int] = {}
        for f in integrity_open:
            kinds[f.get("kind") or "other"] = kinds.get(f.get("kind") or "other", 0) + 1
        for kind in ("unsigned_write", "current_balance"):
            kinds.setdefault(kind, 0)
        for kind, n in sorted(kinds.items()):
            add("visr_integrity_open", n, kind=kind)
    for d in derates or []:
        add("visr_derate_pct", d.get("value"), asset=d.get("asset"), plc=d.get("plc") or "")

    out = []
    for name, series in rows.items():
        if not series:
            continue
        out.append(f"# HELP {name} {_HELP[name]}")
        out.append(f"# TYPE {name} gauge")
        for labels, value in series:
            lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items() if v is not None)
            out.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")
    return "\n".join(out) + "\n"
