// The plant recording on the wall clock, for the Grafana panels of the console preview (LOG-122).
//
// Grafana draws on wall-clock time, so the recording plays on the wall clock here: plant time
//   t(w) = t0 + ((w - anchor) * speed) mod (t1 - t0)          (w: unix seconds; the recording loops)
// The console reads the same anchor and speed from /clock and computes the same t, so the console
// and Grafana show the same moment. The values come from the console's own modules (dashboard/app,
// mounted read-only): Replay.values for a row and applyOverlay for the mock verdict, so Grafana shows
// exactly the values that the console shows.
//
// Grafana reads this service as a Prometheus datasource. It answers the part of the Prometheus HTTP
// API that the console's panels use, computed from the recording for any wall time (past windows
// are full at once, no warm-up):
//   /api/v1/query_range, /api/v1/query   the selector visr_value{tag="<tag>"}; "1+1" (health check)
//   /api/v1/labels, /api/v1/label/<name>/values, /api/v1/status/buildinfo
// A real Prometheus can still scrape it:
//   /metrics   visr_value{tag, station} for every numeric tag now, visr_replay_seconds
//   /clock     JSON: { anchor, speed, t0, t1, mock }
//
// Environment: VISR_APP (default /app), VISR_REPLAY (default /data/replay.json), VISR_ANCHOR
// (unix s, default the start of the process), VISR_SPEED (default 1), VISR_MOCK (default 1),
// PORT (default 9108).
//
// Live mode (2026-10-22, VISR_LIVE=<the plant service, live/plant_service.py --floor>): the values come
// from the live plant's floor recording, appended every second (Replay.append, as the console does),
// on the plant's own clock: t(w) = anchor_t + (w - anchor) * speed, no loop and no mock verdict. A
// wall time before the first row or after the newest row takes the nearest row.
import http from "node:http";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const APP = process.env.VISR_APP || "/app";
const { Replay } = await import(pathToFileURL(`${APP}/floor/replay.js`).href);
const { applyOverlay } = await import(pathToFileURL(`${APP}/ui/data/model.js`).href);

const LIVE = (process.env.VISR_LIVE || "").replace(/\/$/, "");
const getJSON = (u) => fetch(u).then((r) => (r.ok ? r.json() : null)).catch(() => null);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let rep, clock = null;
if (LIVE) {
  let seg = await getJSON(`${LIVE}/floor`);
  while (!seg || !seg.values.rows.length) { await sleep(2000); seg = await getJSON(`${LIVE}/floor`); }   // the plant warms up
  rep = new Replay({ meta: seg.meta, states: seg.states, values: seg.values, events: seg.events, items: seg.items });
  clock = await getJSON(`${LIVE}/clock`);
  let busy = false;
  setInterval(async () => {
    if (busy) return; busy = true;
    const s = await getJSON(`${LIVE}/floor?since=${rep.t1}`);
    if (s) { try { rep.append(s); } catch (e) { console.error(String(e)); } }
    busy = false;
  }, 1000);
  setInterval(async () => { clock = (await getJSON(`${LIVE}/clock`)) || clock; }, 5000);
} else {
  rep = new Replay(JSON.parse(readFileSync(process.env.VISR_REPLAY || "/data/replay.json", "utf8")));
}
const ANCHOR = Number(process.env.VISR_ANCHOR || Math.floor(Date.now() / 1000));
const SPEED = Number(process.env.VISR_SPEED || 1);
const MOCK = !LIVE && process.env.VISR_MOCK !== "0";
const SPAN = rep.t1 - rep.t0;
const MAX_POINTS = 11000;                       // the Prometheus limit per series
const plantT = LIVE
  ? (w) => (clock?.anchor ? clock.anchor_t + (w - clock.anchor) * clock.speed : rep.t1)
  : (w) => rep.t0 + ((((w - ANCHOR) * SPEED) % SPAN) + SPAN) % SPAN;
const esc = (s) => String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
const num = (v) => (typeof v === "boolean" ? Number(v) : v);

// the values of every tag at wall time w, as the console shows them
function valuesAt(w) {
  const t = plantT(w);
  return applyOverlay({ values: rep.values(t) }, t - rep.t0, MOCK).values;
}

function metrics() {
  const out = ["# HELP visr_value A plant tag of the recording at the replay time (the mock verdict applied).", "# TYPE visr_value gauge"];
  for (const [tag, v] of Object.entries(valuesAt(Date.now() / 1000))) {
    const x = num(v);
    if (typeof x === "number" && Number.isFinite(x)) out.push(`visr_value{tag="${esc(tag)}",station="${esc(tag.split(".")[0])}"} ${x}`);
  }
  out.push("# HELP visr_replay_seconds Plant time since the start of the recording.", "# TYPE visr_replay_seconds gauge",
    `visr_replay_seconds ${(plantT(Date.now() / 1000) - rep.t0).toFixed(1)}`);
  return out.join("\n") + "\n";
}

// ── the Prometheus query API, for the selector visr_value{tag="..."} ────────────────────────────
const SELECTOR = /^\s*visr_value\s*\{\s*tag\s*=\s*"([^"]+)"\s*\}\s*$/;
const time = (s, d) => { if (s == null || s === "") return d; const n = Number(s); return Number.isFinite(n) ? n : Date.parse(s) / 1000; };
function duration(s) {
  const n = Number(s); if (Number.isFinite(n)) return n;
  const m = /^(\d+(?:\.\d+)?)(ms|s|m|h|d)$/.exec(s || ""); if (!m) return NaN;
  return Number(m[1]) * { ms: 0.001, s: 1, m: 60, h: 3600, d: 86400 }[m[2]];
}
const labelsOf = (tag) => ({ __name__: "visr_value", tag, station: tag.split(".")[0] });
const ok = (data) => ({ status: "success", data });
const bad = (error) => ({ status: "error", errorType: "bad_data", error });

function queryRange(p) {
  const m = SELECTOR.exec(p.get("query") || "");
  if (!m) return bad("this replay answers only visr_value{tag=\"<tag>\"}");
  const tag = m[1], now = Date.now() / 1000;
  const start = time(p.get("start")), end = Math.min(time(p.get("end"), now), now), step = duration(p.get("step"));
  if (!Number.isFinite(start) || !Number.isFinite(end) || !(step > 0)) return bad("start, end and step are needed");
  if ((end - start) / step > MAX_POINTS) return bad("exceeded maximum resolution of 11,000 points per timeseries");
  if (!rep.vTags.includes(tag)) return ok({ resultType: "matrix", result: [] });
  const values = [];
  for (let w = start; w <= end + 1e-9; w += step) {
    const x = num(valuesAt(w)[tag]);
    if (typeof x === "number" && Number.isFinite(x)) values.push([Number(w.toFixed(3)), String(x)]);
  }
  return ok({ resultType: "matrix", result: values.length ? [{ metric: labelsOf(tag), values }] : [] });
}

function query(p) {
  const q = (p.get("query") || "").trim(), w = time(p.get("time"), Date.now() / 1000);
  if (/^[\d\s.+\-*/()]+$/.test(q)) {                     // a constant (Grafana's health check: 1+1)
    const v = Function(`"use strict"; return (${q});`)();
    return ok({ resultType: "scalar", result: [w, String(v)] });
  }
  const m = SELECTOR.exec(q);
  if (!m) return bad("this replay answers only visr_value{tag=\"<tag>\"}");
  const x = num(valuesAt(w)[m[1]]);
  return ok({ resultType: "vector", result: typeof x === "number" && Number.isFinite(x) ? [{ metric: labelsOf(m[1]), value: [w, String(x)] }] : [] });
}

function api(path, p) {
  if (path === "/api/v1/query_range") return queryRange(p);
  if (path === "/api/v1/query") return query(p);
  if (path === "/api/v1/labels") return ok(["__name__", "station", "tag"]);
  if (path === "/api/v1/label/__name__/values") return ok(["visr_value"]);
  if (path === "/api/v1/label/tag/values") return ok(rep.vTags);
  if (path === "/api/v1/label/station/values") return ok([...new Set(rep.vTags.map((x) => x.split(".")[0]))]);
  if (path === "/api/v1/status/buildinfo") return ok({ version: "2.53.0", revision: "visr-replay", branch: "visr-replay" });
  return null;
}

function send(res, code, type, body) {
  res.writeHead(code, { "Content-Type": type, "Cache-Control": "no-store" });
  res.end(body);
}

http.createServer((req, res) => {
  const url = new URL(req.url, "http://replay");
  if (url.pathname === "/metrics") return send(res, 200, "text/plain; version=0.0.4", metrics());
  if (url.pathname === "/clock") {
    return send(res, 200, "application/json", JSON.stringify(LIVE ? { ...clock, live: true, t0: rep.t0, t1: rep.t1, mock: false }
      : { anchor: ANCHOR, speed: SPEED, t0: rep.t0, t1: rep.t1, mock: MOCK }));
  }
  if (url.pathname === "/healthz") return send(res, 200, "text/plain", "ok\n");
  if (!url.pathname.startsWith("/api/v1/")) return send(res, 404, "text/plain", "not found\n");
  let body = "";
  req.on("data", (c) => { body += c; if (body.length > 1 << 16) req.destroy(); });
  req.on("end", () => {
    const p = new URLSearchParams(url.search);
    if (req.method === "POST") for (const [k, v] of new URLSearchParams(body)) p.set(k, v);
    let out;
    try { out = api(url.pathname, p); } catch (e) { out = bad(String(e.message || e)); }
    if (!out) return send(res, 404, "application/json", JSON.stringify(bad("not supported")));
    send(res, out.status === "success" ? 200 : 400, "application/json", JSON.stringify(out));
  });
}).listen(Number(process.env.PORT || 9108), () => console.log(LIVE ? `replay exporter, live: ${rep.vTags.length} tags from ${LIVE}`
  : `replay exporter: ${rep.vTags.length} tags, ${SPAN} s loop, speed ${SPEED}, mock ${MOCK}`));
