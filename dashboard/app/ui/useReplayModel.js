"use client";
// One clock for the console (LOG-120, LOG-122): plant time t advances, the replay gives the snapshot,
// the mock verdict overlay applies, every attached floor scene gets the snapshot each frame, and
// React gets the view-model twice a second. Acknowledged alarms are kept here.
//
// Two clocks:
//   server  the preview on forge answers /api/clock (deploy/preview/replay-exporter.mjs). The console
//           then plays the recording on the wall clock, with the server's anchor and speed:
//           t = t0 + ((now - anchor) * speed) mod (t1 - t0). Grafana draws the same values on the
//           same wall clock, so a panel and the console show the same moment. No pause, no speeds.
//   local   no /api/clock (the dev server): the browser replays at the chosen speed, with pause.
//   live    the live plant (2026-10-22, live/README.md): /api/live/plant is the plant service with its
//           floor recording, /api/live/engine the engine service. The console appends the plant's
//           new segments every second, shows the plant time of the newest sample, and takes the
//           verdict and the incidents from the engine. No pause, no speeds, no mock verdict.
// The replay moves forward only (an earlier t resets its event replay); the recording loops.
import { useEffect, useRef, useState } from "react";
import { Replay, loadReplay } from "../floor/replay";
import { applyOverlay, createModel, liveVerdict } from "./data/model";

let _rep = null;
const getReplay = () => (_rep ||= loadReplay());
const getJSON = (u) => fetch(u, { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
const getClock = () => getJSON("/api/clock");
const LIVE = "/api/live";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// The time of day to show for plant time t: the wall clock when the server sets the clock (so the
// console matches the time axis of Grafana), else the plant time of the recording.
const C = { server: null, t: 0 };
const hms = (s) => [s / 3600, (s % 3600) / 60, s % 60].map((x) => String(Math.floor(x)).padStart(2, "0")).join(":");
export function timeOf(rep, t) {
  if (C.server) {
    const d = new Date(Date.now() - ((C.t - t) / C.server.speed) * 1000);
    return hms(d.getHours() * 3600 + d.getMinutes() * 60 + d.getSeconds());
  }
  return hms(((((rep?.meta?.t_day0_h ?? 0) * 3600 + t) % 86400) + 86400) % 86400);
}

// The live plant: wait for the first rows of the floor recording (the plant warms up first), then
// append a segment every second and read the engine every 2 s. Returns the cleanup.
function startLive(R, { setModel, setError, setLive, alive }) {
  const timers = [];
  let raf = 0;
  (async () => {
    let seg = await getJSON(`${LIVE}/plant/floor`);
    while (alive() && (!seg || !seg.values.rows.length)) {
      setLive({ warming: true });
      await sleep(2000);
      seg = await getJSON(`${LIVE}/plant/floor`);
    }
    if (!alive()) return;
    const rep = new Replay({ meta: seg.meta, states: seg.states, values: seg.values, events: seg.events, items: seg.items });
    R.rep = rep; R.model = createModel(rep, { mock: false });
    R.clock = await getJSON(`${LIVE}/plant/clock`);
    const grafana = await fetch("/grafana/api/health").then((r) => r.ok).catch(() => false);
    setLive({ seed: seg.meta.seed, grafana });
    let busy = false;
    const pull = async () => {
      if (busy) return; busy = true;
      const s = await getJSON(`${LIVE}/plant/floor?since=${rep.t1}`);
      if (s) { try { rep.append(s); } catch (e) { setError(String(e)); } }
      busy = false;
    };
    let n = 0;
    const engine = async () => {
      R.engine = await getJSON(`${LIVE}/engine/state`);
      if (n++ % 5 === 0) R.incidents = (await getJSON(`${LIVE}/engine/incidents`)) || R.incidents;
    };
    timers.push(setInterval(pull, 1000), setInterval(engine, 2000),
      setInterval(async () => { R.clock = (await getJSON(`${LIVE}/plant/clock`)) || R.clock; }, 5000));
    await engine();
    // catch up from the start of the recording, so that the event log and the items are right
    R.t = rep.t1;
    for (let t = rep.t0; t < R.t; t += 10) R.model.step(rep.snapshotAt(t), { acked: R.acked });
    let lastUi = 0;
    const tick = (now) => {
      raf = requestAnimationFrame(tick);
      const c = R.clock;
      const want = c?.anchor ? c.anchor_t + (Date.now() / 1000 - c.anchor) * c.speed : rep.t1;
      R.t = Math.max(R.t, Math.min(want, rep.t1));          // forward only, never past the newest sample
      const snap = rep.snapshotAt(R.t);
      for (const s of R.scenes) { s.timeScale = c?.speed || 1; s.setSnapshot(snap); }
      if (now - lastUi > 500 || R.force) {
        R.force = false; lastUi = now;
        setModel(R.model.step(snap, { acked: R.acked, verdict: liveVerdict(R.engine, rep), incidents: R.incidents }));
      }
    };
    raf = requestAnimationFrame(tick);
  })().catch((e) => setError(String(e)));
  return () => { timers.forEach(clearInterval); cancelAnimationFrame(raf); };
}

export default function useReplayModel({ mock = true, speed = 20, paused = false, startRel = 0 } = {}) {
  const [model, setModel] = useState(null);
  const [error, setError] = useState(null);
  const [server, setServer] = useState(null);
  const [live, setLive] = useState(null);
  const ref = useRef({ rep: null, model: null, t: null, scenes: new Set(), acked: new Set(), speed, paused });
  ref.current.speed = speed; ref.current.paused = paused;

  useEffect(() => {
    let raf = 0, alive = true, stopLive = null;
    getJSON(`${LIVE}/plant/clock`).then((lc) => {
      if (!alive) return null;
      if (lc) { stopLive = startLive(ref.current, { setModel, setError, setLive, alive: () => alive }); return null; }
      return Promise.all([getReplay(), getClock()]);
    }).then((got) => {
      if (!got || !alive) return;
      const [rep, clock] = got;
      if (!alive) return;
      const R = ref.current;
      const span = rep.t1 - rep.t0;
      const serverT = () => rep.t0 + ((((Date.now() / 1000 - clock.anchor) * clock.speed) % span) + span) % span;
      const useMock = clock ? clock.mock : mock;
      C.server = clock; setServer(clock);
      R.rep = rep; R.model = createModel(rep, { mock: useMock });
      R.t = clock ? serverT() : rep.t0 + startRel;
      // catch up from the start of the recording, so that cards and times are right after a jump
      for (let t = rep.t0; t < R.t; t += 10) R.model.step(applyOverlay(rep.snapshotAt(t), t - rep.t0, useMock), { acked: R.acked });
      let last = performance.now(), lastUi = 0;
      const tick = (now) => {
        raf = requestAnimationFrame(tick);
        const dt = Math.min(0.25, (now - last) / 1000); last = now;
        if (clock) R.t = serverT();
        else { if (!R.paused) R.t += dt * R.speed; if (R.t > rep.t1) R.t = rep.t0; }
        C.t = R.t;
        const snap = applyOverlay(rep.snapshotAt(R.t), R.t - rep.t0, useMock);
        const scale = clock ? clock.speed : R.paused ? 0 : R.speed;
        for (const s of R.scenes) { s.timeScale = scale; s.setSnapshot(snap); }
        if (now - lastUi > 500 || R.force) { R.force = false; lastUi = now; setModel(R.model.step(snap, { acked: R.acked })); }
      };
      raf = requestAnimationFrame(tick);
    }).catch((e) => setError(String(e)));
    return () => { alive = false; cancelAnimationFrame(raf); stopLive?.(); };
  }, [mock, startRel]);                                       // eslint-disable-line react-hooks/exhaustive-deps

  const api = {
    rep: ref.current.rep,
    server,
    live,
    attach: (scene) => { ref.current.scenes.add(scene); return () => ref.current.scenes.delete(scene); },
    seek: (tRel) => { const R = ref.current; if (R.rep) { R.t = R.rep.t0 + tRel; R.force = true; } },
    ack: (key) => { ref.current.acked.add(key); ref.current.force = true; },
  };
  return { model, error, ...api };
}
