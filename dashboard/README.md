# dashboard

The VISR dashboard is a Next.js **static export** (no Node server at runtime) served by nginx.
nginx also proxies `/api/` to the in-cluster API gateway (`api.aiops.svc:8088`), so the browser uses
one origin (no CORS, no second exposed port). The page fetches live data from `/api/*`.

Sections: Boot · Causal Monitor (FLOOR and EDGE views) · Machines (plant tiles, trends, SCADA tag
browser) · Pods · Scenarios (PS0, PS1, PS2, PS5) · Recommendations · Audit.

## Local development (laptop)

```bash
cd dashboard
npm ci
npm run dev      # http://localhost:3000, dev-only mock data when /api is not reachable
npm run build    # writes the static export to out/
```

The mock data in `app/page.jsx` is for design review only. The production export never uses it.

## Build and deploy (box)

Create the `visr-auth` and `visr-tls` Secrets before the first deploy. The readiness probe fails
closed without them. `PIVOT_SETUP.md` step 5.0 has the commands.

```bash
docker build -t skn/dashboard:v0.1 dashboard/
docker save skn/dashboard:v0.1 | sudo k3s ctr images import -
kubectl apply -f deploy/dashboard.yaml
kubectl -n aiops rollout restart deploy/dashboard
```

Open `https://<node-ip>:30443`. Port 30080 only redirects to HTTPS. Log in as `viewer` to look, or
as `operator` to fire and reset scenarios. The node IP includes the box's Tailscale address, and
there is no public ingress.
