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

| Path | What |
|---|---|
| `app/page.jsx`, `app/ui/UiApp.jsx` | The console: the top bar, the verdict, attention, the vitals window, the dock and the drawers |
| `app/ui/FloorHud.jsx` | The floor HUD: markers, selection ticks and readout, the hover tip, the fault path, the overlay lines and labels, labels that move apart. It follows the camera on every frame (`onFrame`, `anchorOf`). |
| `app/ui/data/networks.js` | The overlays: power, air, water, gas and the material flow, with their sources in the plant model |
| `app/ui/useReplayModel.js` | One clock (the server clock or the browser replay): the plant recording, the mock verdict overlay, the floor scenes, the view-model to React twice a second, acknowledged alarms, `timeOf` |
| `app/ui/data/model.js` | The view-model: areas, the vital value per station, display bands, attention rules, acknowledgment, the hold (`HOLD_S`), the event log, the mock verdict |
| `app/ui/Glyph.jsx`, `app/ui/styles/` | Status shapes, tokens (light and dark, with contrast figures), the stylesheet |
| `app/floor/` | The 3D floor (LOG-119). Console hooks: `hud` (no sprite plates or floor brackets, a fainter grid), `onFrame`, `anchorOf`, `setRoles`, `setFocusSet`, `frame`. Floor themes: `shop`, `shopDark` (the Dark theme), `console` (old look, `/floor-lab` only). |
| `app/legacy/page.jsx` | The previous console, unchanged, until its files are removed from this branch |

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

## The previous operator console (LOG-062, at /legacy)

The dashboard is one static screen with no page scroll. Every panel has a fixed place, and only
panel bodies scroll. The primary target is 1920×1080 at 100 % zoom. From 1280 to 1599 px wide, or
from 700 to 859 px tall, the console uses compact sizes. Below 1280×700, the panels stack and the
page scrolls.

The layout reads left to right in the order of the engine pipeline:

| Region | Panels |
|---|---|
| Top | **Command bar**: the VISR plate, the text size buttons, the refresh clock, and lamps for engine, aggregator, SCADA, PLC link, historian, fleet, auth, the audit chain, and integrity. Only an abnormal lamp shows by itself. The healthy lamps fold behind one pill that counts them |
| Left column | **Assets** (machines grouped by rail and coolant loop) and **Event log** (the audit ledger). The event log says "Scenario 1" where the API says PS1 (LOG-078) |
| Center | **Map** (FLOOR or EDGE, 3D or PLAN camera, FOCUS) and the detail tabs: **Selected**, **Fleet**, **Tags**, **Trends**, **Edge** |
| Right column | **Verdict** (STEADY, FORECAST, ROOT CAUSE, INCIDENT, or RECOVERING, from `/api/incident`: the current driver, "started by" the origin, the reason, the chain, the narrative, the last phases, and an **Ask** box), which takes the free height, and **Actions** (Execute, holding, and the advisory suggestions) |

- The console has no fault controls and never says that a fault is injected (LOG-081). Faults run
  from the shell on the box: `ssh -t forge 'bash ~/Tata_InnoVent/deploy/faults.sh'` (see the script
  header). The console shows only what the plant and the engine show.
- The gaps between the panels and the columns are drag handles. Drag one to resize, and double-click
  it to reset that size. The sizes stay in this browser only (`localStorage` key `visr.layout`).

- The floor map uses a normal orbit camera (three.js OrbitControls, LOG-063): drag rotates, and the
  scene follows the cursor. Right-drag pans, and the wheel moves the camera in and out. A rotation
  never changes the distance. 3D and PLAN are camera presets, and a second click on the active preset
  resets the view.
- The camera is a perspective camera with a 38° vertical field of view (LOG-104). A near machine shows
  larger than a far one. A preset moves the camera along its line of sight until the whole hall fits
  the map. The camera moves only when the operator moves it. The map has no fog. The clip planes
  follow the camera, so a close zoom never cuts or hides a machine.
- `MOTION` in `Floor.jsx` is on (LOG-105): energy and coolant flows move at measured speeds, and pulses
  march along the causal path, at 30 frames per second. With `MOTION` off, the map draws a frame only
  when data, the selection, the pointer, or the camera changes, and a causal path shows fixed
  arrowheads from cause to effect. `MOTION` ignores the browser's reduced-motion setting. Windows
  reports reduced motion when its "Animation effects" setting is off.
- A machine or a rail in warning or alarm keeps its value plate on the map. The selected asset and the
  asset under the pointer show theirs too. The plates draw on top of the scene.
- FOCUS gives the map the whole screen. Esc leaves it.
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
| `app/Floor.jsx`, `app/Graph.jsx` | The 3D plant floor (picking, 3D and PLAN) and the edge force graph |
| `app/Fold.jsx` | The fold toggle and the `visr.*` preference hook |
| `app/scifi.css` | The theme layer. It loads after `globals.css` and overrides only what it needs |
| `app/*.jsx` | One file per panel or tab |
| `app/floor/`, `app/floor-lab/` | The floor prototype (LOG-119): the new 3D floor in two looks, played from a plant recording. Not part of the console yet. |

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
npm run dev      # http://localhost:3000, dev-only mock data when /api is not reachable
npm run build    # writes the static export to out/
```

The mock data in `app/lib/mock.js` is for design review only. The production export never uses it.
In the dev build, the command bar has a `mock` switch with seven states: `incident` (PS1 root
cause), `forecast` (PS5 trip ETAs), `steady`, `chain` (PS2), `network` (PS3), `integrity` (PS4A and
PS4B), and `blind` (PS6). The URL parameter `?mock=` sets the same state.

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
