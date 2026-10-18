"use client";
// One clock for the console (LOG-120): plant time t advances, the replay gives the snapshot, the
// mock verdict overlay applies, every attached floor scene gets the snapshot each frame, and React
// gets the view-model twice a second. The replay moves forward only (an earlier t resets its event
// replay), except when the operator moves the time slider. Acknowledged alarms are kept here.
import { useEffect, useRef, useState } from "react";
import { loadReplay } from "../floor/replay";
import { applyOverlay, createModel } from "./data/model";

let _rep = null;
const getReplay = () => (_rep ||= loadReplay());

export default function useReplayModel({ mock = true, speed = 20, paused = false, startRel = 0 } = {}) {
  const [model, setModel] = useState(null);
  const [error, setError] = useState(null);
  const ref = useRef({ rep: null, model: null, t: null, scenes: new Set(), acked: new Set(), speed, paused });
  ref.current.speed = speed; ref.current.paused = paused;

  useEffect(() => {
    let raf = 0, alive = true;
    getReplay().then((rep) => {
      if (!alive) return;
      const R = ref.current;
      R.rep = rep; R.model = createModel(rep, { mock });
      R.t = rep.t0 + startRel;
      // catch up from the start of the recording, so that cards and times are right after a jump
      for (let t = rep.t0; t < R.t; t += 10) R.model.step(applyOverlay(rep.snapshotAt(t), t - rep.t0, mock), { acked: R.acked });
      let last = performance.now(), lastUi = 0;
      const tick = (now) => {
        raf = requestAnimationFrame(tick);
        const dt = Math.min(0.25, (now - last) / 1000); last = now;
        if (!R.paused) R.t += dt * R.speed;
        if (R.t > rep.t1) R.t = rep.t0;                          // the recording loops
        const snap = applyOverlay(rep.snapshotAt(R.t), R.t - rep.t0, mock);
        for (const s of R.scenes) { s.timeScale = R.paused ? 0 : R.speed; s.setSnapshot(snap); }
        if (now - lastUi > 500 || R.force) { R.force = false; lastUi = now; setModel(R.model.step(snap, { acked: R.acked })); }
      };
      raf = requestAnimationFrame(tick);
    }).catch((e) => setError(String(e)));
    return () => { alive = false; cancelAnimationFrame(raf); };
  }, [mock, startRel]);                                       // eslint-disable-line react-hooks/exhaustive-deps

  const api = {
    rep: ref.current.rep,
    attach: (scene) => { ref.current.scenes.add(scene); return () => ref.current.scenes.delete(scene); },
    seek: (tRel) => { const R = ref.current; if (R.rep) { R.t = R.rep.t0 + tRel; R.force = true; } },
    ack: (key) => { ref.current.acked.add(key); ref.current.force = true; },
  };
  return { model, error, ...api };
}
