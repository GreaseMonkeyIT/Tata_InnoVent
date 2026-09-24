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
| Left column | **Assets** (machines grouped by rail and coolant loop) and **Event log** (the audit ledger). The event log says "Scenario 1" where the API says PS1 (LOG-078) |
| Center | **Map** (FLOOR or EDGE, ISO or PLAN camera) and the detail tabs: **Selected**, **Fleet**, **Tags**, **Trends**, **Edge** |
| Right column | **Verdict** (STEADY, FORECAST, or ROOT CAUSE), which takes the free height, and **Actions** (Execute, holding, advisory) |

- The console has no fault controls and never says that a fault is injected (LOG-081). Faults run
  from the shell on the box: `ssh -t forge 'bash ~/Tata_InnoVent/deploy/faults.sh'` (see the script
  header). The console shows only what the plant and the engine show.
- The gaps between the panels and the columns are drag handles. Drag one to resize, and double-click
  it to reset that size. The sizes stay in this browser only (`localStorage` key `visr.layout`).

- The floor map uses a normal orbit camera (three.js OrbitControls, LOG-063): drag rotates, and the
  scene follows the cursor. Right-drag pans, and the wheel zooms. A rotation never changes the zoom.
  ISO and PLAN are camera presets, and a second click on the active preset resets the view.
- A click on a machine, a rail, or the coolant loop (in Assets or on the map) opens it in the
  Selected tab. A click on a PLC cabinet on the map opens its card in the Fleet tab.
- Until the operator picks an asset, Selected follows the root cause, then the soonest forecast,
  then the first rail. A label on the right says which one: "Auto · root cause",
  "Auto · forecast", or "Auto mode". After a pick, a blue button replaces the label. It says
  "Go to root cause" when there is a root cause, else "Auto mode". A click returns to auto mode.
- The Selected tab has three parts: the current values, one Grafana trend of this asset only, and
  its measured SCADA tags. The trend is panel 4 of the `skn-plant` dashboard with `var-asset` set to
  the asset (`deploy/grafana-plant-dashboard.yaml`). A machine shows its draw and, if cooled, its
  temperature with the trip line. A rail shows its voltage and feeder meter. The loop shows its flow.
  The dev build shows a placeholder in place of the frame.
- The Tags tab is a search box and one row per tag: quality glyph, tag, value, quality, and address.
  The table scrolls under a fixed header. The search matches the tag, the asset, the signal, and the
  address. A derived tag shows "calc" as its address, and the row tooltip holds its formula.
- The number under the root in the Verdict is not a probability (LOG-086). With a root edge it is
  labeled "strength": the link strength, a running average that moves 40 % toward 1 each pass the
  engine sees the link again and loses 10 % each pass it does not. Without an edge it is labeled
  "share": the root's share of the candidate scores, 1.00 when only one candidate is left. The row
  tooltip says which.
- Text size: the A, A+, and A++ buttons in the command bar scale the text in every panel body to
  100, 115, or 130 %. The panels, the command bar, the headers, and the tab bar keep their size, and
  a body scrolls when its text needs more room. The choice stays in this browser (`visr.text`).
  In `globals.css` every font size is `calc(<px> * var(--fz))`, and `--fz` is set only in `.pnl-b`.
- The Selected trend colors each metric: draw blue, temperature orange, trip red dashed, volts
  purple, feeder light blue dashed, flow green, load yellow. Each axis takes its series color. The
  current axes start at 0, and the voltage axis spans at least 330 to 400 V, so sensor noise stays
  flat. Every sample is still drawn, and a value outside the window stretches the axis.
- The Edge tab groups the workload cards by role in a fixed order: AIOps engine, Plant and SCADA,
  PLC fleet, Observability, then any other namespace. Inside a group the cards sort by name, so a
  state change never moves a card. A gauge shows use as a share of the limit, else of the request
  (amber above 100 %, never red). A workload with neither shows its absolute use and "no quota".
- The line under the asset name shows its kind as chips: for a machine, "machine", its rail, and
  "cooled" or "uncooled". A tripped machine gets a red chip.
- No dialog covers the console. Execute asks for confirmation inside its Actions card. Add PLC
  opens as a form inside the Fleet tab. Remove needs a second click within 5 s.

### Color roles

The console uses the Stage 2 deck palette sparsely on a near-black navy ground.

| Color | Role |
|---|---|
| Teal `#12C6B3` | A normal status marker (glyphs, map lamps, PLC RUN), and the VISR accent (corner brackets) |
| Gray `#8A93A6` | Normal marks that are not status markers: sparklines, card edges (LOG-080). The normal range in an Assets band is light cyan (LOG-085) |
| Blue `#0000B3` | Operator input: command buttons, the brand plate, the active tab, the map selection brackets (blue edge). Fill only, never text. |
| Amber `#FF9C00` | Warning: blast radius, forecast, injected fault, STALE tag |
| Red `#F2495C` | Alarm: root cause, trip, BAD tag, denied action |
| Black `#000000` | A live data well: the map, sparkline tracks, inputs |

Every status also has a shape (● normal, ▲ warning, ■ alarm, ⌀ tripped), so color is never the only
signal. Only the status markers take a color (teal, amber, red). Everything else is gray. Labels
are dim and values are bright. Panels show values and states only, with no explanation text.
A lamp or a panel header shows text only when its state is abnormal (for example "CHAIN BROKEN").
The tokens live in `app/globals.css`. Code that cannot read CSS variables (three.js, SVG)
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
