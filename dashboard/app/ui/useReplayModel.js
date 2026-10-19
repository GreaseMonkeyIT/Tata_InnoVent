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
// The replay moves forward only (an earlier t resets its event replay); the recording loops.
import { useEffect, useRef, useState } from "react";
import { loadReplay } from "../floor/replay";
import { applyOverlay, createModel } from "./data/model";

let _rep = null;
const getReplay = () => (_rep ||= loadReplay());
const getClock = () => fetch("/api/clock", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);

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

export default function useReplayModel({ mock = true, speed = 20, paused = false, startRel = 0 } = {}) {
  const [model, setModel] = useState(null);
  const [error, setError] = useState(null);
  const [server, setServer] = useState(null);
  const ref = useRef({ rep: null, model: null, t: null, scenes: new Set(), acked: new Set(), speed, paused });
  ref.current.speed = speed; ref.current.paused = paused;

  useEffect(() => {
    let raf = 0, alive = true;
    Promise.all([getReplay(), getClock()]).then(([rep, clock]) => {
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
    return () => { alive = false; cancelAnimationFrame(raf); };
  }, [mock, startRel]);                                       // eslint-disable-line react-hooks/exhaustive-deps

  const api = {
    rep: ref.current.rep,
    server,
    attach: (scene) => { ref.current.scenes.add(scene); return () => ref.current.scenes.delete(scene); },
    seek: (tRel) => { const R = ref.current; if (R.rep) { R.t = R.rep.t0 + tRel; R.force = true; } },
    ack: (key) => { ref.current.acked.add(key); ref.current.force = true; },
  };
  return { model, error, ...api };
}
