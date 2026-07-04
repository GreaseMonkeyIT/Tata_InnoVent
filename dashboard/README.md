# dashboard (P6)

Next.js **static export** (no Node server at runtime) served by nginx, which also reverse-proxies
`/api/` to the in-cluster api gateway (`api.aiops.svc:8088`) — so the browser uses a single origin
(no CORS, no second exposed port). Data is fetched client-side from `/api/graph`, `/api/narrative`,
and `/api/health`. The causal graph is React Flow + dagre: edge width from `render_weight`, animated
+ hot when `state=active`, grey when `source=memory` (the steady backbone), root node highlighted.
The scenario console fires `POST /api/scenarios/S1/trigger`; PSI heatmaps/signals live in Grafana
(linked, not re-implemented here).

## Local dev (on the laptop — node/npm live there)

```bash
cd dashboard && npm install && npm run build   # emits out/ (the static export)
```

## Build & deploy (on the box — docker/kubectl live there)

```bash
docker build -t skn/dashboard:v0.1 dashboard/ && docker save skn/dashboard:v0.1 | sudo k3s ctr images import -
./deploy/skctl up --mode solo                  # applies deploy/dashboard.yaml (NodePort 30080)
```

Reachable at `http://<NODE_IP>:30080` — including the box's Tailscale IP from any tailnet peer, with
no public ingress.
