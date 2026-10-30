// Playback of a plant recording (plant/floor/replay.py) for the floor prototype (2026-10-18).
// `snapshotAt(t)` gives what the line's control system knows at plant time t: each station's state
// word (and since when), the measured values of the last sample, and the place of each tracked ID
// (from the read and transfer events up to t).

const lastLE = (arr, t) => {           // index of the last entry with arr[i][0] <= t, or -1
  let lo = 0, hi = arr.length - 1, k = -1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (arr[mid][0] <= t) { k = mid; lo = mid + 1; } else hi = mid - 1; }
  return k;
};

export class Replay {
  constructor(rec) {
    this.rec = rec;
    this.meta = rec.meta;
    this.items = rec.items;
    this.t0 = rec.meta.t_start;
    this.t1 = rec.meta.t_end;
    // states per station
    this.st = {};
    for (const [t, name, word] of rec.states) (this.st[name] ||= []).push([t, word]);
    // values
    const V = rec.values;
    this.vTags = V.tags; this.vT0 = V.t0; this.vDt = V.dt; this.rows = V.rows;
    this.ramMax = {};
    for (const n of ["lmp-1", "press-1"]) {
      const k = V.tags.indexOf(`${n}.ram_mm`);
      if (k >= 0) this.ramMax[n] = Math.max(1, ...V.rows.map((r) => r[k] ?? 0));
    }
    this.events = rec.events;
    this.version = 0;          // grows with every appended segment (the live plant)
    this._reset();
  }

  // A segment of the live plant (live/plant_service.py GET /floor?since=t1): the states, value rows
  // and tracking events after t1, in the form of a recording. The rows continue the rows held.
  append(seg) {
    for (const [t, name, word] of seg.states) (this.st[name] ||= []).push([t, word]);
    const V = seg.values;
    if (V.rows.length) {
      const k = Math.round((V.t0 - this.vT0) / this.vDt);
      if (k !== this.rows.length) throw new Error(`live rows do not continue: row ${k}, held ${this.rows.length}`);
      for (const r of V.rows) this.rows.push(r);
      for (const n of Object.keys(this.ramMax)) {
        const j = this.vTags.indexOf(`${n}.ram_mm`);
        for (const r of V.rows) this.ramMax[n] = Math.max(this.ramMax[n], r[j] ?? 0);
      }
    }
    for (const e of seg.events) this.events.push(e);
    Object.assign(this.items, seg.items);
    this.t1 = seg.meta.t_end;
    this.version++;
  }

  _reset() {
    this.cur = 0;
    this._tApplied = -Infinity;
    this.where = new Map();    // id -> { place, since, from, info }
    this.flags = new Map();     // id -> { formed, blasted, coated }
    this.history = new Map();   // id -> [ [t, kind, src, dst, info] ]
    this.shipped = { frames: 0, engines: 0 };
    this.lastDrop = {};         // crane -> place of its last drop
    this.joined = new Map();    // id -> the ID it joined
  }

  _flag(id, k) { const f = this.flags.get(id) || {}; f[k] = true; this.flags.set(id, f); }

  _apply(ev) {
    const [t, kind, id, src, dst, info] = ev;
    (this.history.get(id) || this.history.set(id, []).get(id)).push([t, kind, src, dst, info]);
    if (kind === "new") {
      this.where.set(id, { place: dst, since: t, from: null, info });
      // a CED load or a frame takes the process flags of its parts
      const parts = info?.parts || (info?.from ? [info.from] : null);
      if (parts) for (const p of parts) { const f = this.flags.get(p); if (f) this.flags.set(id, { ...(this.flags.get(id) || {}), ...f }); }
    } else if (kind === "move") {
      const prev = this.where.get(id);
      const from = prev?.place ?? src;
      if (from === "lmp-1") this._flag(id, "formed");
      if (from === "blast-1") this._flag(id, "blasted");
      if (from === "ced-1:ced" || from === "ced-1:hoist>uf") this._flag(id, "coated");
      if (from?.startsWith("crane-") && !dst.startsWith("crane-")) this.lastDrop[from] = dst;
      this.where.set(id, { place: dst, since: t, from, info });
    } else if (kind === "request") {
      const w = this.where.get(id);
      if (w) w.waiting = info?.by;
    } else if (kind === "join") {
      this.where.delete(id);
      this.joined.set(id, info?.into);
    } else if (kind === "ship") {
      this.where.delete(id);
      if (id.startsWith("FR")) this.shipped.frames++; else this.shipped.engines++;
    }
  }

  _advance(t) {
    if (t < this._tApplied) this._reset();
    while (this.cur < this.events.length && this.events[this.cur][0] <= t) this._apply(this.events[this.cur++]);
    this._tApplied = t;
  }

  state(name, t) {
    const a = this.st[name]; if (!a) return { word: null, since: this.t0 };
    const k = lastLE(a, t); return k < 0 ? { word: null, since: this.t0 } : { word: a[k][1], since: a[k][0] };
  }

  values(t) {
    const i = Math.max(0, Math.min(this.rows.length - 1, Math.floor((t - this.vT0) / this.vDt)));
    if (i === this._vi) return this._v;
    const row = this.rows[i], v = {};
    this.vTags.forEach((k, j) => { v[k] = row[j]; });
    this._vi = i; this._v = v;
    return v;
  }

  snapshotAt(t) {
    this._advance(t);
    const states = {};
    for (const name of Object.keys(this.st)) states[name] = this.state(name, t);
    const items = [];
    for (const [id, w] of this.where) items.push({ id, ...w, type: this.items[id]?.type, flags: this.flags.get(id) || {}, meta: this.items[id] });
    const cranes = {};
    for (const c of ["crane-1", "crane-2"]) {
      const onHook = items.find((x) => x.place === c);
      cranes[c] = { ...states[c], item: onHook?.id || null, src: onHook?.from || null, dst: onHook?.info?.to || null,
        hookSince: onHook?.since ?? null, lastDrop: this.lastDrop[c] || null };
    }
    return { t, states, values: this.values(t), items, cranes, shipped: { ...this.shipped } };
  }

  historyOf(id) { return this.history.get(id) || []; }
}

export async function loadReplay(url = "/floor/replay.json") {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return new Replay(await r.json());
}
