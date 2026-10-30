"use client";
// Floor prototype (2026-10-18): the plant recording played back on the new 3D floor, in two looks.
// Not part of the console yet. Open /floor-lab in the dev build; the recording comes from
// plant/floor/replay.py (console/public/floor/replay.json).
import { useEffect, useMemo, useRef, useState } from "react";
import { FloorScene } from "../floor/FloorScene";
import { loadReplay } from "../floor/replay";
import { classify, derived } from "../floor/states";
import { BY_NAME } from "../floor/layout";
import { THEMES } from "../floor/themes";

const SPEEDS = [1, 5, 20, 60];
const clock = (meta, t) => {
  const s = ((meta?.t_day0_h ?? 0) * 3600 + t) % 86400;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
};
const PLACE_TEXT = (p) => (p || "").replace("pallet-1:transfer", "in transit on the pallet line").replace("ced-1:hoist>", "hoist to ")
  .replace(":in", " input").replace(":out", " output").replace(":", " ");

const UI = {
  console: { bg: "#04060b", panel: "rgba(10, 14, 24, 0.92)", line: "rgba(120,170,255,0.25)", fg: "#d2d6e4", dim: "#8a93a6", on: "#12c6b3", btn: "#11151f" },
  shop: { bg: "#d6dbe1", panel: "rgba(255,255,255,0.94)", line: "rgba(40,52,66,0.18)", fg: "#1d232b", dim: "#5b6470", on: "#1677ff", btn: "#eef1f4" },
};

const lampCss = (theme, cls) => "#" + (THEMES[theme].status[cls] ?? 0).toString(16).padStart(6, "0");

export default function FloorLab() {
  const wrap = useRef(null), sceneRef = useRef(null), repRef = useRef(null);
  const [theme, setTheme] = useState("shop");
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(20);
  const [labels, setLabels] = useState(false);
  const [ids, setIds] = useState(false);
  const [sel, setSel] = useState(null);
  const [debug, setDebug] = useState(false);
  const tRef = useRef(0);

  // load the recording and the theme from the URL (?theme=console|shop&t=seconds&speed=n)
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get("theme") && THEMES[q.get("theme")]) setTheme(q.get("theme"));
    if (q.get("speed")) setSpeed(Number(q.get("speed")));
    if (q.get("pause")) setPlaying(false);
    if (q.get("debug")) setDebug(true);
    if (q.get("labels")) setLabels(true);
    if (q.get("ids")) setIds(true);
    if (q.get("sel")) setSel(q.get("sel"));
    loadReplay().then((r) => {
      repRef.current = r;
      const t0 = q.get("t") ? r.t0 + Number(q.get("t")) : r.t0;
      tRef.current = t0; setT(t0); setReady(true);
    }).catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => {
    if (!ready || !wrap.current) return;
    const s = new FloorScene(wrap.current, { theme, onPick: (h) => setSel(h?.id || null) });
    s.setRamMax(repRef.current.ramMax);
    s.setLabels(labels, ids); s.select(sel);
    const q = new URLSearchParams(window.location.search);
    if (q.get("view")) s.view(q.get("view"));
    sceneRef.current = s;
    window.__floor = s; window.__rep = repRef.current; // for inspection from the browser console
    return () => { s.dispose(); sceneRef.current = null; };
  }, [ready]);                                         // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { sceneRef.current?.setTheme(theme); }, [theme]);
  useEffect(() => { sceneRef.current?.setLabels(labels, ids); }, [labels, ids]);
  useEffect(() => { sceneRef.current?.select(sel); }, [sel]);

  // the plant clock
  useEffect(() => {
    if (!ready) return;
    let raf, last = performance.now(), lastUi = 0;
    const tick = (now) => {
      raf = requestAnimationFrame(tick);
      const r = repRef.current, s = sceneRef.current;
      const dt = Math.min(0.25, (now - last) / 1000); last = now;
      if (playing) tRef.current = Math.min(r.t1, tRef.current + dt * speed);
      if (s) { s.timeScale = playing ? speed : 0; s.setSnapshot(r.snapshotAt(tRef.current)); }
      if (now - lastUi > 200) { lastUi = now; setT(tRef.current); }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [ready, playing, speed]);

  const ui = UI[theme];
  const r = repRef.current;
  const snap = useMemo(() => (r ? r.snapshotAt(t) : null), [r, t]);

  const btn = (on) => ({ background: on ? ui.on : ui.btn, color: on ? "#fff" : ui.fg, border: `1px solid ${ui.line}`, borderRadius: 4,
    padding: "4px 10px", font: "12px 'Segoe UI', Arial, sans-serif", cursor: "pointer" });

  return (
    <div style={{ position: "fixed", inset: 0, background: ui.bg, color: ui.fg, font: "13px 'Segoe UI', Arial, sans-serif" }}>
      <div ref={wrap} style={{ position: "absolute", inset: 0 }} />
      {err && <div style={{ position: "absolute", top: 60, left: 20, color: "#e0333d" }}>Recording not found: {err}. Run plant/floor/replay.py first.</div>}
      {/* top bar */}
      <div style={{ position: "absolute", top: 10, left: 10, right: 10, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
        background: ui.panel, border: `1px solid ${ui.line}`, borderRadius: 6, padding: "6px 10px" }}>
        <b style={{ marginRight: 6 }}>Floor prototype</b>
        {Object.values(THEMES).map((th) => <button key={th.name} style={btn(theme === th.name)} onClick={() => setTheme(th.name)}>{th.label}</button>)}
        <span style={{ width: 10 }} />
        <button style={btn(false)} onClick={() => setPlaying(!playing)}>{playing ? "pause" : "play"}</button>
        {SPEEDS.map((s) => <button key={s} style={btn(speed === s)} onClick={() => setSpeed(s)}>{s}×</button>)}
        {r && <input type="range" min={r.t0} max={r.t1} step={1} value={t} style={{ width: 260 }}
          onChange={(e) => { tRef.current = Number(e.target.value); setT(tRef.current); }} />}
        {r && <span style={{ fontFamily: "Consolas, monospace" }}>{clock(r.meta, t)}</span>}
        <span style={{ width: 10 }} />
        {["site", "frame", "cross", "ced", "engine", "plan"].map((v) => <button key={v} style={btn(false)} onClick={() => sceneRef.current?.view(v)}>{v}</button>)}
        <span style={{ width: 10 }} />
        <button style={btn(labels)} onClick={() => setLabels(!labels)}>station labels</button>
        <button style={btn(ids)} onClick={() => setIds(!ids)}>IDs</button>
        {snap && <span style={{ marginLeft: "auto", color: ui.dim }}>shipped: {snap.shipped.frames} frames · {snap.shipped.engines} engines since the start of the run</span>}
      </div>
      {/* legend */}
      <div style={{ position: "absolute", left: 10, bottom: 10, background: ui.panel, border: `1px solid ${ui.line}`, borderRadius: 6, padding: "8px 10px", lineHeight: 1.7 }}>
        {[["active", "working"], ["idle", "ready, waits for a part"], ["setup", "a person must act"], ["warn", "abnormal"], ["fault", "fault or trip"]].map(([c, txt]) => (
          <div key={c}><span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 5, marginRight: 8, background: lampCss(theme, c), border: `1px solid ${ui.line}` }} />{txt}</div>
        ))}
        <div style={{ marginTop: 4, color: ui.dim }}>solid part: at a read point or in a station · see-through: between read points</div>
        <div style={{ color: ui.dim }}>cranes: placed from their transfer (the sim has no crane position)</div>
      </div>
      {debug && <div id="floor-debug" style={{ position: "absolute", right: 10, bottom: 10, maxWidth: 700, whiteSpace: "pre-wrap", font: "11px Consolas, monospace", background: ui.panel, padding: 8 }}>
        {(() => { const s = sceneRef.current; if (!s) return "no scene"; const e = s.lastError; const i = s.renderer.info.render;
          let meshes = 0, vis = 0; s.scene.traverse((o) => { if (o.isMesh) { meshes++; if (o.visible) vis++; } });
          const lm = s.mach?.["lmp-1"]?.built.root; const fm = lm?.children[0];
          const wp = fm ? fm.getWorldPosition(new fm.position.constructor()).toArray().map((x) => x.toFixed(1)).join(",") : "-";
          return `frames ${s.frames || 0} · calls ${i.calls} · tris ${i.triangles} · items ${s.itemMeshes?.size} · meshes ${meshes}/${vis} · scene children ${s.scene.children.length}\n` +
            `lmp root ${lm?.position.toArray().join(",")} first mesh ${wp} parent ${lm?.parent === s.scene} cam ${s.camera.position.toArray().map((x) => x.toFixed(0)).join(",")} far ${s.camera.far}\n${e ? e.stack : "no error"}`; })()}
      </div>}
      {/* selection */}
      {snap && sel && <Detail snap={snap} sel={sel} rep={r} ui={ui} theme={theme} onClose={() => setSel(null)} />}
    </div>
  );
}

function Detail({ snap, sel, rep, ui, theme, onClose }) {
  const m = BY_NAME[sel];
  const box = { position: "absolute", right: 10, top: 60, width: 340, maxHeight: "calc(100% - 80px)", overflow: "auto",
    background: ui.panel, border: `1px solid ${ui.line}`, borderRadius: 6, padding: 12 };
  const row = (k, v) => <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 10 }}><span style={{ color: ui.dim }}>{k}</span><span style={{ fontFamily: "Consolas, monospace" }}>{v}</span></div>;
  if (m) {
    const s = snap.states[sel];
    const c = (s?.word != null ? classify(sel, s.word) : derived(sel, snap.values)) || classify(sel, null);
    const vals = Object.entries(snap.values).filter(([k]) => k.startsWith(`${sel}.`));
    return (
      <div style={box}>
        <div style={{ display: "flex", justifyContent: "space-between" }}><b>{sel}</b><button onClick={onClose} style={{ border: 0, background: "none", color: ui.dim, cursor: "pointer" }}>✕</button></div>
        <div style={{ color: ui.dim, marginBottom: 8 }}>{m.label}</div>
        <div style={{ marginBottom: 8 }}><span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 5, marginRight: 8, background: lampCss(theme, c.cls) }} />{c.text} <span style={{ color: ui.dim }}>({s?.word ?? "no state tag"})</span></div>
        {s?.since != null && row("since", `${Math.round(snap.t - s.since)} s`)}
        {vals.map(([k, v]) => row(k.slice(sel.length + 1), v == null ? "—" : typeof v === "number" ? v.toFixed(2) : String(v)))}
        {m.kind === "crane" && <div style={{ marginTop: 8, color: ui.dim }}>The bridge is drawn at the places of its transfer. The sim has no crane position: a real crane gives it from lasers or encoders on some cranes only.</div>}
        {["plasma-1", "weld-1", "weld-2"].includes(sel) && <div style={{ marginTop: 8, color: ui.dim }}>The torch or robot motion shows the state only. The sim has no axis positions for it.</div>}
      </div>
    );
  }
  const it = snap.items.find((x) => x.id === sel);
  const hist = rep.historyOf(sel);
  const meta = rep.items[sel] || {};
  return (
    <div style={box}>
      <div style={{ display: "flex", justifyContent: "space-between" }}><b>{sel}</b><button onClick={onClose} style={{ border: 0, background: "none", color: ui.dim, cursor: "pointer" }}>✕</button></div>
      <div style={{ color: ui.dim, marginBottom: 8 }}>{meta.type}{meta.carrier ? ` · carrier ${meta.carrier}` : ""}{meta.pallet ? ` · pallet ${meta.pallet}` : ""}</div>
      {it ? row("now", PLACE_TEXT(it.place)) : row("now", "joined into another part, or shipped")}
      {it && row("since", `${Math.round(snap.t - it.since)} s`)}
      {it?.flags && row("done", [it.flags.formed && "formed", it.flags.blasted && "blasted", it.flags.coated && "CED coated"].filter(Boolean).join(", ") || "—")}
      {meta.parts && row("parts", meta.parts.join(", "))}
      <div style={{ marginTop: 10, marginBottom: 4, color: ui.dim }}>reads and transfers</div>
      {hist.filter((h) => h[0] <= snap.t).map((h, i) => (
        <div key={i} style={{ fontFamily: "Consolas, monospace", fontSize: 12, color: h[1] === "request" ? ui.dim : ui.fg }}>
          {clock(rep.meta, h[0])} {h[1] === "move" ? `→ ${PLACE_TEXT(h[3])}` : h[1] === "new" ? `new at ${PLACE_TEXT(h[3])}` : h[1] === "join" ? `joined ${h[4]?.into}` : h[1] === "ship" ? "shipped" : `asks ${h[4]?.by}`}
          {h[4]?.by && h[1] === "move" ? ` (${h[4].by})` : ""}{h[4]?.decay_pa != null ? ` ${h[4].decay_pa} Pa ${h[4].ok ? "pass" : "FAIL"}` : ""}
        </div>
      ))}
    </div>
  );
}
