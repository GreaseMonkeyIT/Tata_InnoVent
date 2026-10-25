# dashboard

The VISR dashboard is a Next.js **static export** (no Node server at runtime) served by nginx.
nginx also proxies `/api/` to the in-cluster API gateway (`api.aiops.svc:8088`), so the browser uses
one origin (no CORS, no second exposed port). The page fetches live data from `/api/*`.

## The console (LOG-120 to LOG-122: Prototype B)

The console is calm by default. The 3D floor (the Shop look) fills the screen, and boxed,
translucent windows sit on it: the top bar, Verdict (top left), the vitals window with the dock (bottom), and Attention (top
right, only while a station is abnormal). An abnormal state brings its notification, a marker over
the station and a glyph in the vitals window. Recovery takes them out again: the item shows
"Recovering" for 2 min of plant time, then it leaves. An alarm blinks until someone acknowledges it,
and an acknowledged alarm leaves at once when it recovers. There is no close button on an item.
Everything else is in a drawer at the right, one click or one key away: 1 Stations, 2 Events,
3 Parts, 4 Tags, 5 Trends, 6 Energy, and Legend. The settings icon in the top bar opens Settings.
Esc closes the floor view or the overlay, then the drawer, then the selection.

Overlays (the top bar, "Overlay"): Power, Air, Water, Gas and Flow show one network of the plant
sim on the floor, in the manner of the Dyson Sphere Program power view. The other stations fade, a
line runs from each source to each user, and each member shows one value (kW, pressure,
temperatures, gas flow, or the tracked IDs at a station). The topology comes from
`plant/model/plant.py` and `plant/model/flow.py` (`app/ui/data/networks.js`). The sim has no cable,
pipe or conveyor routes, so the lines are a schematic. A small window at the top centre names the
overlay. Labels that would overlap move apart.

Rules (ideas.md 22.6, the design language of Dyson Sphere Program and EVE Online from
`references/ui_2026-10-18/research_hud_references.md`):

- Windows are boxed: a 1 px border, square corners, hard edges. The fill is 88 % with a 10 px
  blur of the floor behind it. The setting "Window fill: Solid" makes it opaque.
- Names, states, tags and values are plain text, never in a box. Acknowledge is the one boxed
  button. Other commands are blue text. Tooltips are small windows (`data-hint`), not the browser
  title.
- Gray and ink for normal. Red (square) and orange (filled triangle, ink outline) for the abnormal
  only, on the glyph and on the abnormal value. Blue for commands, the active choice and selection.
- On the floor: a marker with a stalk and a halo label over each abnormal station, four corner ticks
  for the selection, a dashed path for "Show on the floor".
- Sentence case and tabular numerals. No glow, gradients, shadows or uppercase labels.
- Every graph is a Grafana panel (operator order, LOG-122), never a chart of the console's own:
  the attention items, Inspect (the vital, or any value picked in its table) and Trends. The
  panel is the dashboard `visr-console` of the preview Grafana, on the same origin (`/grafana/`),
  in Grafana's own default look and the console's light or dark theme. Its window is relative
  (`from=now-15m` or `now-30m`, `to=now`) with `refresh=5s`, so it keeps scrolling. The frame URL
  holds no time, so a re-render of the console never reloads it. Without Grafana (the dev server),
  a graph shows one line that says where Grafana runs.

Clocks: with the preview on forge, the console reads `/api/clock` and plays the recording on the
wall clock, with the anchor and the speed of the replay exporter. Grafana draws the same values on
the same wall clock, so a panel and the console show the same moment. The top bar then shows the
time of day and has no pause or speeds. Without `/api/clock` (the dev server), the browser replays
the recording with pause and speeds, and the clock shows the plant time.

Live mode (2026-10-22, `live/README.md`): when `/api/live/plant/clock` answers, the console runs on
the live plant instead of a recording. It loads the floor recording of the plant service
(`GET /floor`, the same form as `replay.json`), appends the new part every second
(`Replay.append`), and shows the plant time of the newest sample. The verdict comes from the engine
service (`/api/live/engine/state`, every 2 s): the newest open incident with a named root
(`liveVerdict` in `app/ui/data/model.js`), and the incidents go into the event log. The display
bands come from the first 2 h of rows (the lead of the run). There is no mock verdict, no pause and
no speed choice. The dev server proxies `/api/live/plant` and `/api/live/engine` to the plant and
engine services on the laptop (`next.config.mjs`, `VISR_PLANT`, `VISR_ENGINE`); the preview's nginx
proxies them on forge (GET only).

| Path | What |
|---|---|
| `app/page.jsx`, `app/ui/UiApp.jsx` | The console: the top bar, the verdict, attention, the vitals window, the dock and the drawers |
| `app/ui/FloorHud.jsx` | The floor HUD: markers, selection ticks and readout, the hover tip, the fault path, the overlay lines and labels, labels that move apart. It follows the camera on every frame (`onFrame`, `anchorOf`). |
| `app/ui/data/networks.js` | The overlays: power, air, water, gas and the material flow, with their sources in the plant model |
| `app/ui/useReplayModel.js` | One clock (the live plant, the server clock or the browser replay): the plant recording, the mock verdict overlay, the floor scenes, the view-model to React twice a second, acknowledged alarms, `timeOf` |
| `app/ui/data/model.js` | The view-model: areas, the vital value per station, display bands, attention rules, acknowledgment, the hold (`HOLD_S`), the event log, the mock verdict, the engine's verdict in live mode (`liveVerdict`, `incidentLog`) |
| `app/ui/Glyph.jsx`, `app/ui/styles/` | Status shapes, tokens (light and dark, with contrast figures), the stylesheet |
| `app/floor/` | The 3D floor (LOG-119). Console hooks: `hud` (no sprite plates or floor brackets, a fainter grid), `onFrame`, `anchorOf`, `setRoles`, `setFocusSet`, `frame`. Floor themes: `shop`, `shopDark` (the Dark theme), `console` (old look, `/floor-lab` only). |

The prototype runs on the plant recording (`plant/floor/replay.py` writes
`public/floor/replay.json`) and on a MOCK verdict (an air header loss at compressor-1 from 08:00,
recovery at 09:00). The top bar says both. Do not ship `/` to the box before the overhaul API
serves the same view-model.

URL parameters: `theme=light|dark`, `mode=live|energy`,
`drawer=stations|events|parts|tags|trends|energy|legend|settings|inspect`, `sel=<station or ID>`,
`show=1` (open the floor view of the verdict), `overlay=power|air|water|gas|flow`,
`fill=glass|solid` (the window fill),
`chamfer=0|1` (the cut drawer corner),
`floor=dark|light` (the floor of the Dark theme), `t=<seconds after the recording start>`,
`speed=<n>`, `pause=1`, `verdict=steady` (no mock verdict). The browser keeps the theme and the
settings (`visr.theme`, `visr.ui`), so pass `theme` and `chamfer` for repeatable screenshots.

### The console preview on forge

The preview runs the console without the API, the cluster or a login, as three Docker containers on
the network `visr-preview`:

| Container | What |
|---|---|
| `visr-replay` | `deploy/preview/replay-exporter.mjs` on `node:22-slim`: the plant recording played on the wall clock. It is Grafana's datasource: it answers the Prometheus query API (`/api/v1/query_range`, `/api/v1/query`) for the selector `visr_value{tag="<tag>"}`, computed from the recording for each panel's window. It also serves `/clock` for the console and `/metrics` for a real Prometheus later. It uses the console's own modules (`app/floor/replay.js`, `applyOverlay`), mounted read-only, so Grafana shows the values that the console shows. |
| `visr-grafana` | Grafana with the datasource and the dashboard `visr-console` (`deploy/preview/grafana/`), anonymous viewer, embedding on, served from `/grafana/`, no update checks, a random admin password that nobody keeps |
| `visr-console-preview` | The static export (`Dockerfile.preview`). Its nginx (`nginx.preview.conf`) proxies `/grafana/` and `/api/clock` on the same origin. |

There is no Prometheus container: the Prometheus image of the k3s store had lost its packed layers,
and the replay can answer the queries of the panels directly. The Grafana image comes from the
paused k3s stack. Copy it into Docker once (the script reads the k3s store files and does not start
k3s):

```bash
ssh -t forge 'sudo python3 ~/VISR_Overhaul/deploy/k3s-images-to-docker.py'
```

Then, on forge (the plant recording must be in `public/floor/`, Syncthing copies it):

```bash
bash ~/VISR_Overhaul/deploy/console-preview.sh
```

The script builds `visr-overhaul/console:<date-time>` and replaces the three containers. Only the
console publishes a port: 3121 on the Tailscale address of forge (not on the LAN). The replay clock
starts when the script runs, at `VISR_START` seconds into the recording (default 0). The mock
incident starts at 3600 s and recovers at 7200 s, so `VISR_START=3300` shows it five minutes after
the start. Each Grafana panel shows only its own window (15 or 30 min); the window is full at once,
because the replay computes the past values of the recording. The containers have no restart policy.
`STOP=1 bash ~/VISR_Overhaul/deploy/console-preview.sh` removes them. `GRAFANA_IMAGE`, `VISR_SPEED`
and `VISR_MOCK` override the defaults. The script does not touch k3s, the local registry or any image
or namespace of the main stack.

`LIVE=1 bash ~/VISR_Overhaul/deploy/console-preview.sh` runs the same preview on the live plant. The
plant service (`--floor`) and the engine service (the newest `eval/results/*/live-pack.pkl`, or
`VISR_PACK`) run on forge itself from `~/visr-venv`, in the screen sessions `visr-live-plant` and
`visr-live-engine` (logs `/var/tmp/visr-live-*.log`). They listen on the docker0 address
(172.17.0.1) only. The replay exporter serves the live plant to Grafana (`VISR_LIVE`). `VISR_SEED`
(default 60000, a validation day), `VISR_SPEED` (default 1) and `VISR_FAULTS` (for example
`"F6:0.5:900"`, seconds after the warm-up) set the run. The plant warms up first (9 h of plant time
at full speed, a few minutes on forge). A fault during the run comes from a shell on forge:

```bash
curl -X POST http://172.17.0.1:9110/fault -H "Content-Type: application/json" -d '{"fid": "F6", "severity": 0.5}'
```

## Floor prototype (LOG-119)

The page `/floor-lab` plays a plant recording on the new 3D floor. It is a prototype for the
operator's pick of a look (ideas.md section 20). The console does not use it yet.

1. Record a run: `python plant/floor/replay.py --seed 3 --hours 4 --warmup-h 3`. The script writes
   `dashboard/public/floor/replay.json` (gitignored).
2. Start the dev build and open `http://localhost:3000/floor-lab`.

URL parameters: `theme=shop|console`, `t=<seconds after the warm-up>`, `speed=<n>`, `pause=1`,
`view=site|frame|cross|ced|engine|plan`, `labels=1`, `ids=1`, `sel=<station or ID>`, `debug=1`.

A hidden browser tab pauses WebGL frames. For screenshots, use headless Edge with
`--headless=new --enable-unsafe-swiftshader --use-angle=swiftshader --virtual-time-budget=20000`.

## Local development (laptop)

```bash
cd dashboard
npm ci
npm run dev      # http://localhost:3000, plays public/floor/replay.json
npm run build    # writes the static export to out/
```

## Build and deploy (box)

Create the `visr-auth` and `visr-tls` Secrets before the first deploy. The readiness probe fails
closed without them. `PIVOT_SETUP.md` step 5.0 has the commands.

```bash
make push ONLY="dashboard"          # build and push to the box registry (PIVOT_SETUP.md step 4)
kubectl apply -f deploy/dashboard.yaml
kubectl -n aiops rollout restart deploy/dashboard
```

Open `https://<node-ip>:30443`. Port 30080 only redirects to HTTPS. Log in as `viewer` to look, or
as `operator` to execute and restore actions and to add, load, run, stop, or remove PLCs. Faults
run from the fault shell on the box, never from the console. The node IP includes the box's Tailscale address, and
there is no public ingress.
