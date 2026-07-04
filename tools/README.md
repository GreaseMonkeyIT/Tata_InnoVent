# tools/

Small standalone utilities that **read** the live cluster — no changes to the running system.

## `pod-resources.html` — allocated vs live, per pod

A single self-contained HTML page (vanilla JS + inline SVG, no external libs, offline-clean) that
shows, for every pod, **what it's allocated** (CPU/memory requests + limits) next to **what it's
consuming right now**, on a time window that scrolls as new samples arrive.

It reads straight from Prometheus — the same metrics the API's `/api/recommendations` already uses
(`kube_pod_container_resource_{requests,limits}`, `rate(container_cpu_usage_seconds_total[1m])`,
`container_memory_working_set_bytes`). Nothing is written; no rebuild, no product change.

### Run it (on the box)

Pick either connection path, then set the **Prometheus base URL** field to match.

**A · port-forward** (simplest; relies on Prometheus' default CORS):

```bash
kubectl port-forward -n observability svc/prom-kube-prometheus-stack-prometheus 9090:9090
# open tools/pod-resources.html in a browser; leave base URL = http://localhost:9090
```

**B · kubectl proxy** (zero-CORS — serves the page and Prometheus from one origin):

```bash
kubectl proxy --www=tools --www-prefix=/ui/ --port=8001
# browse to  http://localhost:8001/ui/pod-resources.html
# set base URL (relative, no host) to:
#   /api/v1/namespaces/observability/services/prom-kube-prometheus-stack-prometheus:9090/proxy
```

If the status dot goes red with a network/CORS error on path A, switch to path B.

### Controls

- **Namespace regex** — defaults to `factory-.*`; widen to `.*` to see everything.
- **Refresh** — poll interval (2–10s).
- **Window** — how much history the sparkline keeps before it scrolls (2–10 min).
- **Sort** — by name, CPU %, or MEM % of limit.
- **Pause** — freeze polling.

Bars are scaled to the limit; the purple tick marks the request. Green `<70%` of limit, orange
`70–90%`, red `≥90%`. Pods with no limit auto-scale the bar to request/peak and show `lim —`.

> Next step (only if asked): embed this into the Next.js dashboard as a panel/route.
