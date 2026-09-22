import { useRef } from "react";

// Ring buffers for the console sparklines. One history serves every panel, so Assets and
// Selected draw the same samples. The hook pushes one sample for each new plant object, during
// render. The identity check keeps a repeated render (React strict mode) from pushing twice.
const WINDOW = 60; // samples kept per series (about 5 min at the 5 s poll)

export default function useHistory(plant) {
  const buf = useRef(new Map());
  const seen = useRef(null);
  if (plant?.devices && plant !== seen.current) {
    seen.current = plant;
    const H = buf.current;
    const push = (k, v) => {
      if (v == null) return;
      let a = H.get(k);
      if (!a) { a = []; H.set(k, a); }
      a.push(v);
      if (a.length > WINDOW) a.shift();
    };
    for (const [rn, r] of Object.entries(plant.rails || {})) push("r/" + rn, r.volts);
    if (plant.loop) push("loop", plant.loop.flow);
    for (const [dn, d] of Object.entries(plant.devices)) {
      push(`m/${dn}/a`, d.amps);
      push(`m/${dn}/t`, d.temp);
      push(`m/${dn}/p`, d.throughput);
      push(`m/${dn}/s`, d.speed_pct);
    }
  }
  return (k) => buf.current.get(k) || [];
}
