"use client";
// The VISR console (LOG-121): Prototype B, the heads-up display, rebuilt in the design language of
// Dyson Sphere Program and EVE Online (references/ui_2026-10-18/research_hud_references.md). Boxed,
// translucent windows sit on the 3D floor; names and keywords inside them are plain text.
// Calm by default: the floor, a short verdict, the vitals bar and the dock. An abnormal state
// brings its notification, its floor marker and its vitals glyph in; recovery takes them out again.
// Everything else is in a drawer at the right edge, one click or one key away (1 to 6). Esc closes
// the floor view (or overlay), then the drawer, then the selection. Overlays (power, air, water, gas,
// flow) show a network of the plant sim on the floor.
import { Fragment, createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { FloorScene } from "../floor/FloorScene";
import { BY_NAME, MACHINES } from "../floor/layout";
import Glyph from "./Glyph";
import FloorHud from "./FloorHud";
import useReplayModel, { timeOf } from "./useReplayModel";
import { AREAS, HOLD_S, VITAL, bandLevel, cap, fmt, label, unitOf, vitalLabel } from "./data/model";
import { OVERLAYS, buildOverlay } from "./data/networks";

const DRAWERS = [["stations", "Stations"], ["events", "Events"], ["parts", "Parts"], ["tags", "Tags"], ["trends", "Trends"], ["energy", "Energy"], ["legend", "Legend"]];
const HOT = 6;                      // drawers 1 to 6 have a hotkey
const MAX_ITEMS = 4;                // attention items on the screen; the rest are in Events
const q = () => (typeof window === "undefined" ? new URLSearchParams() : new URLSearchParams(window.location.search));
const pref = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } };
const keep = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* not kept */ } };
const clockOf = timeOf;                 // wall clock with the preview server, else plant time
const ago = (s) => (s < 90 ? `${Math.max(0, Math.round(s))} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
const vitalText = (s) => (s?.vital && s.vital.value != null ? `${s.vital.label} ${fmt(s.vital.value, s.vital.tag)} ${s.vital.unit}`.trim() : null);

export default function UiApp() {
  const u = useMemo(q, []);
  const [theme, setTheme] = useState(() => u.get("theme") || pref("visr.theme", "light"));
  const [ui, setUi] = useState(() => ({ chamfer: false, darkFloor: true, names: false, opaque: false, ...pref("visr.ui", {}), ...(u.get("fill") ? { opaque: u.get("fill") === "solid" } : {}), ...(u.get("chamfer") ? { chamfer: u.get("chamfer") === "1" } : {}), ...(u.get("floor") ? { darkFloor: u.get("floor") === "dark" } : {}) }));
  const [drawer, setDrawer] = useState(u.get("drawer") || (u.get("mode") === "energy" ? "energy" : null));
  const [sel, setSel] = useState(u.get("sel") || null);
  const [hover, setHover] = useState(null);
  const [show, setShow] = useState(null);                       // "Show on the floor": { ids, from, to, medium }
  const [overlay, setOverlay] = useState(u.get("overlay") || null);   // power | air | water | gas | flow
  const [follow, setFollow] = useState(true);
  const [paused, setPaused] = useState(!!u.get("pause"));
  const [speed, setSpeed] = useState(Number(u.get("speed") || 20));
  const [mock, setMock] = useState(u.get("verdict") !== "steady");
  const [areaFocus, setAreaFocus] = useState(null);
  const D = useReplayModel({ mock, speed, paused, startRel: Number(u.get("t") || 0) });
  const m = D.model, rep = D.rep;
  const gf = useMemo(() => ({ on: !!D.server, theme }), [D.server, theme]);
  const scene = useRef(null);
  const seen = useRef({});
  const ov = overlay && m ? buildOverlay(overlay, m) : null;
  const pickOverlay = (k) => { setOverlay((o) => (o === k ? null : k)); setShow(null); };

  useEffect(() => { document.documentElement.dataset.theme = theme; keep("visr.theme", theme); }, [theme]);
  useEffect(() => { keep("visr.ui", ui); }, [ui]);
  const floorTheme = theme === "dark" && ui.darkFloor ? "shopDark" : "shop";

  const toggle = (k) => setDrawer((d) => (k == null || d === k ? null : k));
  const inspect = (id) => { setSel(id); setDrawer("inspect"); };
  const showOn = (station) => {
    const v = m?.verdict;
    const chain = v && v.state !== "steady" && v.affected.length && (station === v.root || v.affected.includes(station));
    const ids = chain ? [v.root, ...v.affected] : [station];
    setShow(chain ? { ids, from: v.root, to: v.affected, medium: v.medium } : { ids, from: null, to: [], medium: null });
    setOverlay(null);
    scene.current?.frame(ids, { force: true });
  };

  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === "INPUT") return;
      if (e.key === "Escape") { if (show || overlay) { setShow(null); setOverlay(null); } else if (drawer) setDrawer(null); else setSel(null); return; }
      const i = Number(e.key) - 1;
      if (i >= 0 && i < HOT) toggle(DRAWERS[i][0]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer, show, overlay]);                                 // eslint-disable-line react-hooks/exhaustive-deps

  // follow alarms: a NEW root cause eases the camera to the chain (not while the operator moves it)
  const focusOn = useRef(null);
  const rootKey = m?.verdict?.state === "incident" ? `${m.verdict.root}@${m.verdict.opened}` : null;
  useEffect(() => { if (rootKey && rootKey !== focusOn.current) { focusOn.current = rootKey; if (follow) scene.current?.frame(m.focus); } }, [rootKey]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const s = scene.current; if (!s || !m) return;
    s.setRoles(m.roles); s.setFocusSet(show ? show.ids : ov ? ov.members : []); s.select(sel);
  });
  // a new overlay frames its members (the operator can move the camera after that)
  useEffect(() => { if (overlay && m) scene.current?.frame(buildOverlay(overlay, m).members, { force: true }); }, [overlay, !!m]);   // eslint-disable-line react-hooks/exhaustive-deps
  // the URL can open the floor view (show=1), for the screenshots
  const showDone = useRef(false);
  useEffect(() => { if (u.get("show") && !showDone.current && m?.verdict.state === "incident" && scene.current) { showDone.current = true; showOn(m.verdict.root); } });   // eslint-disable-line react-hooks/exhaustive-deps
  // what the operator has seen in a drawer: the dock's attention tick shows only for newer entries
  useEffect(() => { if (drawer && m) seen.current[drawer] = m.t; }, [drawer, m]);
  useEffect(() => { if (areaFocus && drawer === "stations") document.getElementById(`area-${areaFocus}`)?.scrollIntoView({ block: "start" }); }, [areaFocus, drawer]);

  if (D.error) return <div className="floor-wait">Recording not found: {D.error}. Run plant/floor/replay.py first.</div>;

  const cards = m?.cards || [];
  const markers = cards.filter((c) => !c.leaving && BY_NAME[c.station] && (!show || show.ids.includes(c.station)) && (!ov || ov.members.includes(c.station))).map((c) => {
    const st = m.stations[c.station];
    const word = c.root ? "root cause" : c.state === "recover" ? "recovering" : st && (st.cls === "fault" || st.cls === "warn") ? st.text : c.reasons[0]?.toLowerCase();
    return { station: c.station, level: c.state === "recover" ? "recover" : c.level, root: c.root, blink: c.blink, label: `${c.station}, ${word}` };
  });
  const selInfo = (() => {
    if (!sel || !m) return null;
    const s = m.stations[sel];
    if (s) return { id: sel, lines: [`${cap(s.role === "root" ? "root cause" : s.text)}${s.since != null ? `, ${ago(m.t - s.since)}` : ""}`, vitalText(s) || BY_NAME[sel]?.label].filter(Boolean) };
    const it = rep?.items?.[sel];
    if (it) return { id: sel, lines: [it.type] };
    return BY_NAME[sel] ? { id: sel, lines: [BY_NAME[sel].label] } : null;
  })();
  const hoverInfo = hover && m ? { id: hover, line: vitalText(m.stations[hover]) || m.stations[hover]?.text || rep?.items?.[hover]?.type || BY_NAME[hover]?.label } : null;
  const ticks = {};
  if (m) {
    const worst = (xs) => (xs.includes("alarm") ? "alarm" : xs.length ? "warn" : null);
    ticks.events = drawer === "events" ? null : worst(m.events.filter((e) => e.sev !== "info" && e.t > (seen.current.events ?? -Infinity)).map((e) => e.sev));
    ticks.stations = drawer === "stations" ? null : worst(cards.filter((c) => c.state === "active" && c.since > (seen.current.stations ?? -Infinity)).map((c) => c.level));
  }

  return (
    <Gf.Provider value={gf}>
    <div className={`app${ui.opaque ? " opaque" : ""}`}>
      <FloorMap D={D} sceneRef={scene} floorTheme={floorTheme} onPick={(h) => setSel(h?.id || null)} onHover={(h) => setHover(h?.id || null)} />
      {m && <FloorHud sceneRef={scene} markers={markers} names={ui.names ? MACHINES.map((x) => x.name) : []} sel={selInfo} hover={hoverInfo} onInspect={inspect}
        links={show?.from ? show.to.map((to) => ({ from: show.from, to, text: show.medium, dashed: true })) : ov ? ov.links : []} labels={ov ? ov.labels : []} />}
      {(show || ov) && (
        <div className="win notice" role="status">
          <b>{show ? `Showing: ${show.medium ? `${show.medium} path` : show.ids.join(", ")}` : OVERLAYS.find(([k]) => k === overlay)[1]}</b>
          {ov && <span className="n">{ov.note} The lines are a schematic.</span>}
          <button className="cmd" onClick={() => { setShow(null); setOverlay(null); }}>Close</button><span className="muted">Esc</span>
        </div>
      )}
      <Tips />
      <Top m={m} rep={rep} theme={theme} setTheme={setTheme} drawer={drawer} toggle={toggle} follow={follow} setFollow={setFollow}
        paused={paused} setPaused={setPaused} speed={speed} setSpeed={setSpeed} mock={D.server ? D.server.mock : mock} server={D.server} overlay={overlay} pickOverlay={pickOverlay} />
      {m && <Verdict m={m} inspect={inspect} pick={setSel} showOn={showOn} />}
      {m && !drawer && cards.length > 0 && (
        <section className="attn win" aria-label="attention">
          <div className="wh">Attention<span className="sp" /><span className="meta">{cards.filter((c) => c.state === "active").length} abnormal</span></div>
          <div className="wb">
            {cards.slice(0, MAX_ITEMS).map((c) => <Item key={c.key} c={c} m={m} rep={rep} mock={D.server ? D.server.mock : mock} ack={D.ack} inspect={inspect} showOn={showOn} />)}
            {cards.length > MAX_ITEMS && <button className="cmd more" onClick={() => toggle("events")}>{cards.length - MAX_ITEMS} more in Events</button>}
          </div>
        </section>
      )}
      {m && (
        <footer className="bottom win" aria-label="vitals">
          <div className="vit"><Vitals m={m} sel={sel} inspect={inspect} openArea={(id) => { setAreaFocus(id); setDrawer("stations"); }} /></div>
          <Dock drawer={drawer} toggle={toggle} ticks={ticks} />
        </footer>
      )}
      {m && drawer && (
        <Drawer kind={drawer} chamfer={ui.chamfer} m={m} rep={rep} mock={D.server ? D.server.mock : mock} server={D.server} sel={sel} pick={setSel}
          inspect={inspect} showOn={showOn} ack={D.ack} close={() => setDrawer(null)} ui={ui} setUi={setUi} setMock={setMock} openEvents={() => toggle("events")} />
      )}
    </div>
    </Gf.Provider>
  );
}

// Boxed tooltips (a small window over the element) for every element with data-hint. They replace
// the browser's own title tooltip, which ignores the console's look.
function Tips() {
  const [tip, setTip] = useState(null);
  useEffect(() => {
    let timer = 0;
    const over = (e) => {
      const el = e.target.closest?.("[data-hint]");
      clearTimeout(timer);
      if (!el) { setTip(null); return; }
      timer = setTimeout(() => { const r = el.getBoundingClientRect(); setTip({ text: el.dataset.hint, x: r.left + r.width / 2, y: r.top < 80 ? r.bottom : r.top, below: r.top < 80 }); }, 350);
    };
    const out = (e) => { if (!e.relatedTarget?.closest?.("[data-hint]")) { clearTimeout(timer); setTip(null); } };
    const down = () => { clearTimeout(timer); setTip(null); };
    document.addEventListener("pointerover", over); document.addEventListener("pointerout", out); document.addEventListener("pointerdown", down);
    return () => { clearTimeout(timer); document.removeEventListener("pointerover", over); document.removeEventListener("pointerout", out); document.removeEventListener("pointerdown", down); };
  }, []);
  if (!tip) return null;
  return <div className={`win hint${tip.below ? " below" : ""}`} role="tooltip" style={{ left: Math.max(80, Math.min(window.innerWidth - 80, tip.x)), top: tip.y }}>{tip.text}</div>;
}

// ── small parts ──────────────────────────────────────────────────────────────────────────────────
function Choice({ items, value, onChange, label: aria }) {
  return (
    <span className="choice" role="group" aria-label={aria}>
      {items.map(([k, name]) => <button key={k} className={value === k ? "on" : ""} aria-pressed={value === k} onClick={() => onChange(k)}>{name}</button>)}
    </span>
  );
}

const Sliders = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5">
    <path d="M1 4h14M1 12h14" /><circle cx="5" cy="4" r="2" fill="var(--floor-bg)" /><circle cx="11" cy="12" r="2" fill="var(--floor-bg)" />
  </svg>
);
const Cross = () => (<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" stroke="currentColor" strokeWidth="1.5"><path d="M2 2l10 10M12 2L2 12" /></svg>);

// Every graph of the console is a Grafana panel (operator order, 2026-10-19): the preview Grafana on
// the same origin (/grafana/, deploy/preview/), in Grafana's own default look and the console's light
// or dark theme. The window is relative (now-<minutes>m to now) and the panel refreshes every 5 s, so
// it keeps scrolling. The URL holds no time, so a React re-render never reloads the frame.
const Gf = createContext({ on: false, theme: "light" });
function GrafanaPanel({ tag, title, minutes = 15, height = 140 }) {
  const { on, theme } = useContext(Gf);
  const src = `/grafana/d-solo/visr-console/visr-console?orgId=1&panelId=1&var-tag=${encodeURIComponent(tag)}&var-title=${encodeURIComponent(title || tag)}&from=now-${minutes}m&to=now&refresh=5s&theme=${theme}`;
  if (!on) return <div className="gf-off">The graph is a Grafana panel. Grafana runs with the preview on forge (deploy/console-preview.sh).</div>;
  return <iframe className="gf" src={src} height={height} title={`${title || tag}, Grafana`} />;
}
const gfTitle = (tag) => `${vitalLabel(tag)}${unitOf(tag) ? `, ${unitOf(tag)}` : ""}`;

function Band({ b, v, level }) {
  if (!b || v == null) return null;
  const pos = (x) => Math.max(0, Math.min(100, ((x - b.min) / (b.max - b.min)) * 100));
  return (
    <span className={`band ${level || ""}`} data-hint={`normal ${fmt(b.lo)} to ${fmt(b.hi)}`}>
      <span className="nb" style={{ left: `${pos(b.lo)}%`, width: `${pos(b.hi) - pos(b.lo)}%` }} />
      <span className="mk" style={{ left: `${pos(v)}%` }} />
    </span>
  );
}

// ── the floor ────────────────────────────────────────────────────────────────────────────────────
function FloorMap({ D, sceneRef, floorTheme, onPick, onHover, onDown }) {
  const el = useRef(null);
  useEffect(() => {
    if (!el.current) return;
    const s = new FloorScene(el.current, { theme: floorTheme, hud: true, onPick, onHover });
    sceneRef.current = s; window.__floor = s;
    const off = D.attach(s);
    return () => { off(); s.dispose(); sceneRef.current = null; };
  }, []);                                                      // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const s = sceneRef.current; if (s && s.themeName !== floorTheme) s.setTheme(floorTheme); }, [floorTheme]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (D.rep && sceneRef.current) sceneRef.current.setRamMax(D.rep.ramMax); }, [D.rep]);   // eslint-disable-line react-hooks/exhaustive-deps
  return <div className="floor" ref={el} onPointerDown={onDown}>{!D.model && <div className="floor-wait">Loading the plant recording…</div>}</div>;
}

// ── top bar: a solid window, text controls ──────────────────────────────────────────────────────
function Top({ m, rep, theme, setTheme, drawer, toggle, follow, setFollow, paused, setPaused, speed, setSpeed, mock, server, overlay, pickOverlay }) {
  return (
    <header className="top win">
      <span className="brand">VISR</span>
      <Choice label="mode" items={[["live", "Live"], ["energy", "Energy"]]} value={drawer === "energy" ? "energy" : "live"} onChange={(k) => { if ((k === "energy") !== (drawer === "energy")) toggle("energy"); }} />
      <span className="status">{server ? "Plant recording, replayed live" : "Plant recording, not live"}{mock ? ". Mock verdict" : ""}</span>
      <span className="ovl"><span className="muted">Overlay</span><Choice label="overlay" items={OVERLAYS} value={overlay} onChange={pickOverlay} /></span>
      <span className="sp" />
      <label className="check"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />Follow alarms</label>
      <Choice label="theme" items={[["light", "Light"], ["dark", "Dark"]]} value={theme} onChange={setTheme} />
      {!server && (
        <span className="play">
          <button className="cmd" onClick={() => setPaused(!paused)}>{paused ? "Play" : "Pause"}</button>
          <Choice label="speed" items={[[1, "1×"], [20, "20×"], [60, "60×"]]} value={speed} onChange={setSpeed} />
        </span>
      )}
      <span className="clock" data-hint={server ? "Time of day: the recording plays on the wall clock, as in Grafana" : "Plant time of the recording"}>{m && rep ? clockOf(rep, m.t) : "--:--:--"}</span>
      <button className={`icon${drawer === "settings" ? " on" : ""}`} data-hint="Settings" aria-label="Settings" onClick={() => toggle("settings")}><Sliders /></button>
    </header>
  );
}

// ── verdict: a window at the top left ──────────────────────────────────────────────────────────
function Verdict({ m, inspect, pick, showOn }) {
  const v = m.verdict;
  if (v.state === "steady") {
    const last = [...m.events].reverse().find((e) => e.sev !== "info");
    return (
      <section className="verdict win" aria-label="verdict">
        <div className="wh">Verdict</div>
        <div className="wb">
          <div className="v-calm"><Glyph st="ok" size={14} title="normal" />All stations normal</div>
          <div className="v-sub">{last ? `Last abnormal event: ${last.who}, ${last.text}, ${ago(m.t - last.t)} ago` : "No abnormal event in this recording so far"}</div>
        </div>
      </section>
    );
  }
  const inc = v.state === "incident";
  const rootItem = m.cards.find((c) => c.station === v.root);
  return (
    <section className="verdict win" aria-label="verdict">
      <div className="wh">Verdict</div>
      <div className="wb">
      <div className="v-status">{inc ? `Root cause found, since ${ago(m.tRel - v.opened)}` : `Recovering, found ${ago(m.tRel - v.opened)} ago`}</div>
      <div className="v-who"><Glyph st={inc ? "alarm" : "recover"} size={20} blink={!!rootItem?.blink} /><span className="name" onClick={() => inspect(v.root)}>{v.root}</span></div>
      <div className="v-chain">
        <span className="name" onClick={() => pick(v.root)}>{v.root}</span><span className="arrow">→</span><span>{v.medium}</span><span className="arrow">→</span>
        {v.affected.length ? v.affected.map((a) => <span key={a}><Glyph st={inc ? "warn" : "recover"} size={14} /> <span className="name" onClick={() => pick(a)}>{a}</span></span>) : <span className="muted">no station affected yet</span>}
      </div>
      <p className="v-text">{inc ? v.text : v.recoverText}</p>
      <div className="v-strength" data-hint="Strength is the weight of the evidence, not a probability"><span>Strength {Math.round(v.strength * 100)} %</span><span className="bar"><i style={{ width: `${v.strength * 100}%` }} /></span></div>
      {inc && <p className="v-todo"><b>What to do: </b>{v.todo}</p>}
      <div className="cmds"><button className="cmd" onClick={() => showOn(v.root)}>Show on the floor</button><button className="cmd" onClick={() => inspect(v.root)}>Inspect</button></div>
      </div>
    </section>
  );
}

// ── attention: one notification per abnormal station ────────────────────────────────────────────
function itemParts(c, m) {
  const st = m.stations[c.station];
  const tag = c.tags[0] || st?.vital?.tag || null;
  const v = tag ? m.values[tag] : null, b = tag ? m.bands[tag] : null;
  const rec = c.state === "recover";
  return {
    tag, v, b, lvl: tag ? bandLevel(b, v) : null, rec,
    glyph: rec ? "recover" : c.level,
    role: rec ? "Recovering" : c.root ? "Root cause" : c.level === "alarm" ? "Alarm" : "Warning",
    what: rec ? `Back in its normal state, leaves in ${ago(Math.max(0, HOLD_S - (m.t - c.last)))}` : c.reasons[0],
  };
}

function Item({ c, m, rep, mock, ack, inspect, showOn }) {
  const p = itemParts(c, m);
  return (
    <article className={`item${c.leaving ? " leaving" : ""}`} aria-label={`${c.station} ${p.role}`}>
      <div className="i-hd"><Glyph st={p.glyph} size={16} blink={c.blink} /><b className="name" onClick={() => inspect(c.station)}>{c.station}</b><span className="role">{p.role}</span><span className="since">{ago(m.t - c.since)}</span></div>
      <div className="i-what">{p.what}</div>
      {p.tag && rep && (
        <>
          <div className="i-val"><span className="l">{vitalLabel(p.tag)}</span><Band b={p.b} v={p.v} level={p.lvl} /><span className={`v ${p.lvl || ""}`}>{fmt(p.v, p.tag)}<span className="u">{unitOf(p.tag)}</span></span></div>
          <GrafanaPanel tag={p.tag} title={`${c.station}: ${gfTitle(p.tag)}`} height={140} />
        </>
      )}
      <div className="cmds">
        {c.needsAck && <button className="btn" onClick={() => ack(c.key)}>Acknowledge</button>}
        <button className="cmd" onClick={() => inspect(c.station)}>Inspect</button>
        <button className="cmd" onClick={() => showOn(c.station)}>Show on the floor</button>
      </div>
    </article>
  );
}

// ── bottom: the vitals bar and the dock ────────────────────────────────────────────────────────
function Vitals({ m, sel, inspect, openArea }) {
  const val = (p) => (p.value == null ? "—" : p.unit === "" && p.k !== "pf" ? p.value : fmt(p.value, p.tag));
  const areaVal = (v) => v.text ?? (v.value == null ? "—" : typeof v.value === "number" && !Number.isInteger(v.value) ? fmt(v.value) : v.value);
  return (
    <>
      <div className="vit1">
        <span className="key" aria-label="key"><span><Glyph st="alarm" />alarm</span><span><Glyph st="warn" />warning</span><span><Glyph st="recover" />recovering</span></span>
        {m.plant.map((p) => (
          <span key={p.k} className="pv">{p.level && <Glyph st={p.level} size={14} />}<span className="l">{p.label}</span><b className={p.level || ""}>{val(p)}</b>{p.unit && <span className="u">{p.unit}</span>}</span>
        ))}
      </div>
      <div className="vit2">
        {m.areas.map((a) => (
          <div key={a.id} className={`area${a.level ? " abn" : ""}`} onClick={() => openArea(a.id)} role="button" tabIndex={0} data-hint={`${a.name}: open Stations`}>
            <div className="an">{a.level && <Glyph st={a.level} />}{a.name}</div>
            <div className="ag" onClick={(e) => e.stopPropagation()}>
              {a.list.map((s) => <button key={s.name} className={sel === s.name ? "sel" : ""} data-hint={`${s.name}: ${s.role === "root" ? "root cause" : s.text}`} aria-label={s.name} onClick={() => inspect(s.name)}><Glyph st={s.glyph} size={12} /></button>)}
            </div>
            <div className="ac">{a.vitals.map((v, i) => <Fragment key={v.label}>{i ? ", " : ""}{v.label} <b>{areaVal(v)}</b>{v.unit && <span className="u">{v.unit}</span>}</Fragment>)}</div>
          </div>
        ))}
      </div>
    </>
  );
}

function Dock({ drawer, toggle, ticks }) {
  return (
    <nav className="dock" aria-label="drawers">
      {DRAWERS.map(([k, name], i) => (
        <button key={k} className={drawer === k ? "on" : ""} onClick={() => toggle(k)} data-hint={i < HOT ? `${name}, key ${i + 1}` : name}>
          {name}{i < HOT && <sup>{i + 1}</sup>}{ticks[k] && <sup className="tick"><Glyph st={ticks[k]} size={10} title={`new ${ticks[k]} entries`} /></sup>}
        </button>
      ))}
    </nav>
  );
}

// ── drawers ─────────────────────────────────────────────────────────────────────────────────────
function Drawer({ kind, chamfer, m, rep, mock, server, sel, pick, inspect, showOn, ack, close, ui, setUi, setMock, openEvents }) {
  const isPart = kind === "inspect" && !(BY_NAME[sel] || m.stations[sel]);
  const titles = { stations: "Stations", events: "Events", parts: "Parts", tags: "Tags", trends: "Trends", energy: "Energy", legend: "Legend", settings: "Settings", inspect: sel || "Inspect" };
  const st = kind === "inspect" && m.stations[sel];
  const now = m.cards.filter((c) => !c.leaving);
  return (
    <aside className={`drawer win${kind === "energy" ? " wide" : ""}${chamfer ? " chamfer" : ""}`} aria-label={titles[kind]}>
      <div className="wh">
        <h2>{st && <Glyph st={st.glyph} size={16} />}{titles[kind]}</h2>
        <button className="x" onClick={close} data-hint="Close, Esc" aria-label="Close"><Cross /></button>
      </div>
      {now.length > 0 && <AbnormalNow cards={now} m={m} ack={ack} inspect={inspect} showOn={showOn} openEvents={openEvents} />}
      <div className="body">
        {kind === "stations" && <Stations m={m} sel={sel} inspect={inspect} />}
        {kind === "events" && <Events m={m} rep={rep} inspect={inspect} />}
        {kind === "parts" && <Parts m={m} rep={rep} sel={sel} inspect={inspect} />}
        {kind === "tags" && <Tags m={m} />}
        {kind === "trends" && <Trends m={m} rep={rep} mock={mock} sel={sel} />}
        {kind === "energy" && <Energy m={m} rep={rep} />}
        {kind === "legend" && <Legend />}
        {kind === "settings" && <Settings ui={ui} setUi={setUi} mock={mock} setMock={setMock} server={server} />}
        {kind === "inspect" && (isPart ? <PartCard m={m} rep={rep} id={sel} inspect={inspect} /> : <Inspect key={sel} m={m} rep={rep} mock={mock} name={sel} showOn={showOn} />)}
      </div>
    </aside>
  );
}

function AbnormalNow({ cards, m, ack, inspect, showOn, openEvents }) {
  return (
    <section className="now" aria-label="abnormal now">
      <div className="nh">Abnormal now</div>
      {cards.slice(0, MAX_ITEMS).map((c) => {
        const p = itemParts(c, m);
        return (
          <div key={c.key} className="mini">
            <Glyph st={p.glyph} size={16} blink={c.blink} />
            <span><b className="name" onClick={() => inspect(c.station)}>{c.station}</b> <span className="muted">{p.role}</span></span>
            <span className={`v ${p.lvl || ""}`}>{p.tag ? <>{fmt(p.v, p.tag)}<span className="u">{unitOf(p.tag)}</span></> : ""}</span>
            <span className="w">{p.what}, since {ago(m.t - c.since)}</span>
            <span className="cmds">{c.needsAck && <button className="btn" onClick={() => ack(c.key)}>Acknowledge</button>}<button className="cmd" onClick={() => showOn(c.station)}>Show on the floor</button></span>
          </div>
        );
      })}
      {cards.length > MAX_ITEMS && <button className="cmd nh" onClick={openEvents}>{cards.length - MAX_ITEMS} more in Events</button>}
    </section>
  );
}

function Stations({ m, sel, inspect }) {
  return (
    <table className="t">
      <thead><tr><th className="g" /><th>Station</th><th>State</th><th className="r">Vital</th><th>Band</th><th className="r">kW</th></tr></thead>
      <tbody>
        {AREAS.map((a) => {
          const A = m.areas.find((x) => x.id === a.id);
          return (
            <Fragment key={a.id}>
              <tr id={`area-${a.id}`} className="gr"><td colSpan={6}><span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>{A?.level && <Glyph st={A.level} />}{a.name}</span></td></tr>
              {a.stations.map((n) => {
                const s = m.stations[n];
                return (
                  <tr key={n} className={`click${sel === n ? " sel" : ""}`} onClick={() => inspect(n)}>
                    <td className="g"><Glyph st={s.glyph} /></td>
                    <td>{s.level ? <b>{n}</b> : n}</td>
                    <td>{s.role === "root" ? "root cause" : s.text}</td>
                    <td className="r">{s.vital ? <span className={s.vital.level ? `v ${s.vital.level}` : ""}>{fmt(s.vital.value, s.vital.tag)}<span className="u">{s.vital.unit}</span></span> : "—"}</td>
                    <td>{s.vital?.band ? <Band b={s.vital.band} v={s.vital.value} level={s.vital.level} /> : null}</td>
                    <td className="r">{s.kw == null ? "—" : fmt(s.kw)}</td>
                  </tr>
                );
              })}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function Events({ m, rep, inspect }) {
  const list = [...m.events].reverse().slice(0, 200);
  if (!list.length) return <div className="muted">No events yet.</div>;
  return (
    <div className="log">
      {list.map((e, i) => (
        <Fragment key={i}>
          <span className="tm">{clockOf(rep, e.t)}</span>
          {e.sev === "info" ? <span /> : <Glyph st={e.sev} />}
          <span><span className="name" onClick={() => inspect(e.who)}>{e.who}</span> {e.text}</span>
        </Fragment>
      ))}
    </div>
  );
}

function Tags({ m }) {
  const [f, setF] = useState("");
  const rows = Object.entries(m.values).filter(([k]) => !f || k.includes(f));
  return (
    <>
      <input className="field" type="search" placeholder="Filter tags, for example ced-1 or _c" value={f} onChange={(e) => setF(e.target.value)} />
      <table className="t"><thead><tr><th>Tag</th><th className="r">Value</th><th>Unit</th></tr></thead>
        <tbody>{rows.map(([k, v]) => <tr key={k}><td>{k}</td><td className="r">{typeof v === "number" ? fmt(v, k) : String(v)}</td><td className="muted">{unitOf(k)}</td></tr>)}</tbody></table>
    </>
  );
}

function Trends({ m, rep, mock, sel }) {
  const tags = [VITAL[sel]?.tag, `${sel}.power_kw`, "incomer-1.p_kw", "compressor-1.pressure_barg", "ced-1.bath_c", "cool-1.supply_c"]
    .filter((x, i, a) => x && a.indexOf(x) === i && rep?.vTags.includes(x)).slice(0, 5);
  return tags.map((tag) => (
    <div key={tag} style={{ marginBottom: 14 }}>
      <GrafanaPanel tag={tag} title={`${tag.split(".")[0]}: ${gfTitle(tag)}`} minutes={30} height={170} />
    </div>
  ));
}

function Inspect({ m, rep, mock, name, showOn }) {
  const s = m.stations[name];
  const [more, setMore] = useState(false);
  const [chart, setChart] = useState(null);
  if (!s) return <div className="muted">{BY_NAME[name]?.label ? `${BY_NAME[name].label}. ` : ""}No live values for {name} in this recording.</div>;
  const tags = Object.keys(m.values).filter((k) => k.startsWith(`${name}.`));
  return (
    <div>
      <div className="lede">{BY_NAME[name]?.label}</div>
      <div className="kv">
        <span className="k">State</span><span className="v">{s.role === "root" ? "root cause" : s.text}</span>
        {s.since != null && <><span className="k">Since</span><span className="v">{ago(m.t - s.since)}</span></>}
        {s.vital && <><span className="k">{cap(s.vital.label)}</span><span className={`v ${s.vital.level || ""}`}>{fmt(s.vital.value, s.vital.tag)}<span className="u">{s.vital.unit}</span></span></>}
        {s.vital?.band && <><span className="k">Normal band</span><span className="v"><Band b={s.vital.band} v={s.vital.value} level={s.vital.level} /> {fmt(s.vital.band.lo)} to {fmt(s.vital.band.hi)}<span className="u">{s.vital.unit}</span></span></>}
        {s.kw != null && <><span className="k">Power</span><span className="v">{fmt(s.kw)}<span className="u">kW</span></span></>}
      </div>
      {(chart || s.vital) && <div style={{ marginTop: 12 }}><GrafanaPanel tag={chart || s.vital.tag} title={`${name}: ${gfTitle(chart || s.vital.tag)}`} minutes={30} height={200} /></div>}
      <div className="cmds"><button className="cmd" onClick={() => showOn(name)}>Show on the floor</button><button className="cmd" onClick={() => setMore(!more)}>{more ? "Fewer values" : `All ${tags.length} values`}</button></div>
      {more && (
        <table className="t" style={{ marginTop: 10 }}><thead><tr><th>Value, a click shows its graph</th><th className="r">Now</th></tr></thead>
          <tbody>{tags.map((k) => { const v = m.values[k]; return (
            <tr key={k} className={`click${(chart || s.vital?.tag) === k ? " sel" : ""}`} onClick={() => setChart(k)}><td>{label(k)} <span className="muted">{unitOf(k)}</span></td><td className="r">{typeof v === "number" ? fmt(v, k) : String(v)}</td></tr>); })}</tbody></table>
      )}
    </div>
  );
}

const PLACE_TEXT = (p) => (p || "").replace("pallet-1:transfer", "in transit on the pallet line").replace("ced-1:hoist>", "hoist to ").replace(":in", " input").replace(":out", " output").replace(":", " ");

function Parts({ m, rep, sel, inspect }) {
  const [f, setF] = useState("");
  const items = useMemo(() => Object.entries(rep?.items || {}).filter(([id, it]) => ["frame", "engine", "CED load"].includes(it.type) && (!f || id.includes(f))), [rep, f]);
  return (
    <>
      <input className="field" type="search" placeholder="Find a frame, engine or CED load, for example FR26-0004" value={f} onChange={(e) => setF(e.target.value)} />
      <table className="t"><thead><tr><th>ID</th><th>Type</th><th>Last read</th></tr></thead>
        <tbody>{items.slice(0, 80).map(([id, it]) => { const h = rep.historyOf(id).filter((x) => x[0] <= m.t).at(-1); return (
          <tr key={id} className={`click${sel === id ? " sel" : ""}`} onClick={() => inspect(id)}><td>{id}</td><td>{it.type}</td><td className="muted">{h ? `${clockOf(rep, h[0])} ${h[1] === "ship" ? "shipped" : PLACE_TEXT(h[3])}` : "not yet"}</td></tr>); })}</tbody></table>
    </>
  );
}

function PartCard({ m, rep, id, inspect }) {
  const meta = rep?.items?.[id];
  if (!meta) return <div className="muted">Nothing selected.</div>;
  const hist = rep.historyOf(id).filter((h) => h[0] <= m.t);
  const last = hist.at(-1);
  return (
    <div>
      <div className="lede">{meta.type}{meta.carrier ? `, carrier ${meta.carrier}` : ""}{meta.pallet ? `, pallet ${meta.pallet}` : ""}</div>
      <div className="kv"><span className="k">Now</span><span className="v">{!last ? "not yet in the line" : last[1] === "ship" ? "shipped" : last[1] === "join" ? `joined ${last[4]?.into}` : PLACE_TEXT(last[3])}</span>
        {meta.parts && <><span className="k">Parts</span><span className="v">{meta.parts.join(", ")}</span></>}</div>
      <div className="sub">Reads and transfers</div>
      <div className="hist">{hist.map((h, i) => (
        <div key={i} className={h[1] === "request" ? "req" : ""}>{clockOf(rep, h[0])} {h[1] === "move" ? `→ ${PLACE_TEXT(h[3])}` : h[1] === "new" ? `new at ${PLACE_TEXT(h[3])}` : h[1] === "join" ? `joined ${h[4]?.into}` : h[1] === "ship" ? "shipped" : `asks ${h[4]?.by}`}{h[4]?.by && h[1] === "move" ? ` (${h[4].by})` : ""}{h[4]?.decay_pa != null ? ` ${h[4].decay_pa} Pa ${h[4].ok ? "pass" : "FAIL"}` : ""}</div>
      ))}</div>
      {meta.load && <div className="cmds"><button className="cmd" onClick={() => inspect(meta.load)}>Open its CED load {meta.load}</button></div>}
    </div>
  );
}

// ── energy (recorded values and illustrative values are marked) ─────────────────────────────────
function Energy({ m, rep }) {
  const e = useMemo(() => {
    if (!rep) return null;
    const k = (tag) => rep.vTags.indexOf(tag), iP = k("incomer-1.p_kw"), iF = k("incomer-1.pf");
    const n = Math.max(0, Math.min(rep.rows.length, Math.floor((m.t - rep.vT0) / rep.vDt) + 1));
    let kwh = 0, kvah = 0, peak = 0, blk = 0, bn = 0;
    for (let i = 0; i < n; i++) {
      const p = rep.rows[i][iP] ?? 0, pf = Math.max(0.2, rep.rows[i][iF] ?? 1);
      kwh += p * rep.vDt / 3600; kvah += p / pf * rep.vDt / 3600; blk += p / pf; bn++;
      if (bn * rep.vDt >= 900) { peak = Math.max(peak, blk / bn); blk = 0; bn = 0; }
    }
    const st = Object.keys(m.stations).map((s) => { const j = k(`${s}.power_kw`); if (j < 0) return null; let x = 0; for (let i = 0; i < n; i++) x += (rep.rows[i][j] ?? 0) * rep.vDt / 3600; return [s, x]; }).filter(Boolean).filter(([s]) => s !== "incomer-1");
    const sum = st.reduce((a, [, x]) => a + x, 0);
    return { kwh, kvah, peak, st: st.sort((a, b) => b[1] - a[1]), unalloc: kwh - sum, hours: n * rep.vDt / 3600 };
  }, [rep, Math.floor(m.t / 30)]);                            // eslint-disable-line react-hooks/exhaustive-deps
  if (!e) return null;
  return (
    <div>
      <div className="lede">Energy of this recording window ({e.hours.toFixed(1)} h). The energy ledger fills this view later (ideas.md section 21).</div>
      <div className="kv three">
        <span className="k">Site power now</span><span className="v">{fmt(m.values["incomer-1.p_kw"])}<span className="u">kW</span></span><span className="src">recorded</span>
        <span className="k">Energy</span><span className="v">{fmt(e.kwh)}<span className="u">kWh</span></span><span className="src">recorded</span>
        <span className="k">Apparent energy (the billing basis, per kVAh)</span><span className="v">{fmt(e.kvah)}<span className="u">kVAh</span></span><span className="src">recorded</span>
        <span className="k">Peak 15-min demand</span><span className="v">{fmt(e.peak)}<span className="u">kVA</span></span><span className="src">recorded</span>
        <span className="k">Power factor now</span><span className="v">{fmt(m.values["incomer-1.pf"], "x.pf")}</span><span className="src">recorded</span>
        <span className="k">Cost and carbon</span><span className="v">set by the ledger</span><span className="src">illustrative</span>
      </div>
      <div className="sub">Load by station, energy in this window</div>
      <table className="t"><thead><tr><th>Station</th><th className="r">kWh</th><th className="r">Share</th></tr></thead>
        <tbody>{e.st.map(([s, x]) => <tr key={s}><td>{s}{x < 0 ? " (returns power)" : ""}</td><td className="r">{fmt(x)}</td><td className="r">{e.kwh ? `${fmt(100 * x / e.kwh)} %` : "—"}</td></tr>)}
          <tr><td className="wrap">Not allocated (incomer minus stations: building, losses, utilities)</td><td className="r">{fmt(e.unalloc)}</td><td className="r">{e.kwh ? `${fmt(100 * e.unalloc / e.kwh)} %` : "—"}</td></tr></tbody></table>
      <div className="lede" style={{ marginTop: 12 }}>The tariff of this site is per kVAh with a demand charge on the higher of the recorded demand and 75 % of the contract demand (JSERC FY 2026-27). The licensee depends on the site address: an open question (ideas.md 21.6).</div>
    </div>
  );
}

// ── legend and settings ─────────────────────────────────────────────────────────────────────────
function Legend() {
  const row = (st, text) => <div className="row"><Glyph st={st} size={16} /><span>{text}</span></div>;
  return (
    <div className="legend">
      {row("alarm", "Alarm: a fault, a trip or the root cause of an incident. It blinks until someone acknowledges it.")}
      {row("warn", "Warning: a value outside its normal band, or a station that the root cause affects.")}
      {row("recover", "Recovering: back in its normal state. It leaves after 2 min of plant time. An acknowledged alarm leaves at once.")}
      {row("ok", "Running.")}
      {row("idle", "Idle or ready.")}
      {row("busy", "A person must act: load, change or rig.")}
      <div className="sub">On the floor</div>
      <p>The stack lights are the machines&apos; own lamps (IEC 60204-1): green working, white ready, blue a person must act, yellow abnormal, red fault. A station body takes a color only when it is abnormal.</p>
      <p>A marker stands over each abnormal station, with its name and state. Four blue corner ticks mark the selected station.</p>
      <p>The cranes are placed from their transfer order, because the plant sim has no crane position. A part shows solid at a read point and see-through between two read points.</p>
      <div className="sub">The verdict</div>
      <p>Strength is the weight of the evidence, not a probability.</p>
      <div className="sub">Keys</div>
      <p>1 to 6 open a drawer. Esc closes the floor view, then the drawer, then the selection.</p>
    </div>
  );
}

function Settings({ ui, setUi, mock, setMock, server }) {
  return (
    <div className="set">
      {server
        ? <p>Mock verdict {server.mock ? "on" : "off"}: an air header loss at compressor-1, 60 min into each {Math.round((server.t1 - server.t0) / 3600)} h loop of the recording, back after 60 min. The replay server sets it (VISR_MOCK).</p>
        : <label className="check"><input type="checkbox" checked={mock} onChange={(e) => setMock(e.target.checked)} />Mock verdict: an air header loss at compressor-1 from 08:00, back at 09:00</label>}
      <label className="check"><input type="checkbox" checked={ui.names} onChange={(e) => setUi({ ...ui, names: e.target.checked })} />Show the names of all stations on the floor</label>
      <div className="row"><span>Drawer corner</span><Choice label="drawer corner" items={[["s", "Square"], ["c", "Chamfer"]]} value={ui.chamfer ? "c" : "s"} onChange={(k) => setUi({ ...ui, chamfer: k === "c" })} /></div>
      <div className="row"><span>Window fill</span><Choice label="window fill" items={[["t", "Translucent"], ["s", "Solid"]]} value={ui.opaque ? "s" : "t"} onChange={(k) => setUi({ ...ui, opaque: k === "s" })} /></div>
      <div className="row"><span>Floor in the Dark theme</span><Choice label="dark floor" items={[["d", "Dark ground"], ["l", "Light ground"]]} value={ui.darkFloor ? "d" : "l"} onChange={(k) => setUi({ ...ui, darkFloor: k === "d" })} /></div>
      <p className="muted">The window fill, the drawer corner and the dark floor are open design points of this prototype.</p>
    </div>
  );
}
