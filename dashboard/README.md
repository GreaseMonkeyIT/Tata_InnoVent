# dashboard

The VISR dashboard is a Next.js **static export** (no Node server at runtime) served by nginx.
nginx also proxies `/api/` to the in-cluster API gateway (`api.aiops.svc:8088`), so the browser uses
one origin (no CORS, no second exposed port). The page fetches live data from `/api/*`.

## The operator console (LOG-062)

The dashboard is one static screen with no page scroll. Every panel has a fixed place, and only
panel bodies scroll. The primary target is 1920×1080 at 100 % zoom. From 1280 to 1599 px wide, or
from 700 to 859 px tall, the console uses compact sizes. Below 1280×700, the panels stack and the
page scrolls.

The layout reads left to right in the order of the engine pipeline:

| Region | Panels |
|---|---|
| Top | **Command bar**: the VISR plate and lamps for engine, aggregator, PLC link, historian, fleet, auth, and the audit chain |
| Left column | **Assets** (machines grouped by rail and coolant loop) and **Fault injection** (PS0 to PS6 from `/api/scenarios`, each with its incident anchor, and Reset plant for every fault owner) |
| Center | **Map** (FLOOR or EDGE, ISO or PLAN camera) and the detail tabs: **Selected**, **Fleet**, **Tags**, **Trends**, **Edge** |
| Right column | **Verdict** (STEADY, FORECAST, or ROOT CAUSE), **Actions** (Execute, holding, advisory), **Event log** (the audit ledger) |

- The floor map uses a normal orbit camera (three.js OrbitControls, LOG-063): drag rotates, and the
  scene follows the cursor. Right-drag pans, and the wheel zooms. A rotation never changes the zoom.
  ISO and PLAN are camera presets, and a second click on the active preset resets the view.
- A click on a machine, a rail, or the coolant loop (in Assets or on the map) opens it in the
  Selected tab. A click on a PLC cabinet on the map opens its card in the Fleet tab.
- Until the operator picks an asset, Selected follows the root cause, then the soonest forecast,
  then the first rail.
- No dialog covers the console. Execute asks for confirmation inside its Actions card. Add PLC
  opens as a form inside the Fleet tab. Remove needs a second click within 5 s.

### Color roles

The console uses the Stage 2 deck palette sparsely on a near-black navy ground.

| Color | Role |
|---|---|
| Teal `#12C6B3` | The normal state and the VISR accent |
| Blue `#0000B3` | Operator input: command buttons, the brand plate, the active tab. Fill only, never text. |
| Amber `#FF9C00` | Warning: blast radius, forecast, injected fault, STALE tag |
| Red `#F2495C` | Alarm: root cause, trip, BAD tag, denied action |
| Black `#000000` | A live data well: the map, sparkline tracks, inputs |

Every status also has a shape (● normal, ▲ warning, ■ alarm, ⌀ tripped), so color is never the only
signal. The tokens live in `app/globals.css`. Code that cannot read CSS variables (three.js, SVG)
uses `app/lib/palette.js`.

### Trends through `/grafana/`

The Trends tab embeds the Grafana panels `skn-plant` and `skn-psi`. The console is HTTPS, and a
browser blocks a plain-HTTP frame inside it as mixed content. So nginx proxies `/grafana/` to
`prom-grafana.observability.svc`, behind the same login, and Grafana serves from that sub-path.
`deploy/golive.sh` sets the two Grafana settings with `kubectl set env`. The frames mount only
while the Trends tab is open.

## Code map

| Path | What |
|---|---|
| `app/page.jsx` | Boot overlay, then the console |
| `app/Console.jsx` | The shell: the columns, the selection state, the tab state |
| `app/lib/useConsoleData.js` | Every poll, the operator actions, and the values derived from the verdict |
| `app/lib/api.js`, `app/lib/mock.js` | `getJSON` and `send`, and the dev-only design-review mock |
| `app/Floor.jsx`, `app/Graph.jsx` | The 3D plant floor (picking, ISO and PLAN) and the edge force graph |
| `app/*.jsx` | One file per panel or tab |

## Local development (laptop)

```bash
cd dashboard
npm ci
npm run dev      # http://localhost:3000, dev-only mock data when /api is not reachable
npm run build    # writes the static export to out/
```

The mock data in `app/lib/mock.js` is for design review only. The production export never uses it.
In the dev build, the command bar has a `mock` switch with three states: `incident` (PS1 root
cause), `forecast` (PS5 trip ETAs), and `steady`. The URL parameter `?mock=` sets the same state.

## Build and deploy (box)

Create the `visr-auth` and `visr-tls` Secrets before the first deploy. The readiness probe fails
closed without them. `PIVOT_SETUP.md` step 5.0 has the commands.

```bash
make push ONLY="dashboard"          # build and push to the box registry (PIVOT_SETUP.md step 4)
kubectl apply -f deploy/dashboard.yaml
kubectl -n aiops rollout restart deploy/dashboard
```

Open `https://<node-ip>:30443`. Port 30080 only redirects to HTTPS. Log in as `viewer` to look, or
as `operator` to fire and reset scenarios. The node IP includes the box's Tailscale address, and
there is no public ingress.
