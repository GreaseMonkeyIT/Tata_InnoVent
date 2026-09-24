"use client";
import Selected from "./Selected";
import Fleet from "./Fleet";
import Tags from "./Tags";
import Trends from "./Trends";
import Edge from "./Edge";

// The detail tabs (ISA-101 level 3). Only one body mounts at a time, so the Grafana frames and
// the fleet forms cost nothing while their tab is closed. A badge shows a count, and it turns
// amber when something inside needs attention, so a closed tab never hides a problem.
export default function Tabs({ d, tab, setTab, sel, picked, auto, clearPick, focusPlc }) {
  const plcs = d.fleet?.plcs || [];
  const tags = d.scada?.tags || [];
  const pods = d.derived.podRows;
  const TABS = [
    { id: "selected", label: "Selected", badge: sel },
    { id: "fleet", label: "Fleet", badge: plcs.length ? String(plcs.length) : null, warn: plcs.some((p) => p.state !== "RUN") },
    { id: "tags", label: "Tags", badge: tags.length ? String(tags.length) : null, warn: tags.some((t) => t.quality !== "GOOD") },
    { id: "trends", label: "Trends" },
    { id: "edge", label: "Edge", badge: pods.length ? String(pods.length) : null, warn: pods.some((p) => p.st !== "ok") },
  ];
  return (
    <section className="pnl tabs" aria-label="detail tabs">
      <header className="tabbar" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={`tab${tab === t.id ? " on" : ""}`} onClick={() => setTab(t.id)}>
            {t.label}
            {t.badge && <span className={`bdg${t.warn ? " warn" : ""}`}>{t.badge}</span>}
          </button>
        ))}
      </header>
      <div className="pnl-b">
        {tab === "selected" && <Selected plant={d.plant} scada={d.scada} sel={sel} picked={picked} auto={auto} hasRoot={!!d.derived.root?.pod} onClear={clearPick} />}
        {tab === "fleet" && <Fleet fleet={d.fleet} tasks={d.tasks} profiles={d.profiles} plant={d.plant} onChanged={d.fleetChanged} focus={focusPlc} />}
        {tab === "tags" && <Tags scada={d.scada} />}
        {tab === "trends" && <Trends />}
        {tab === "edge" && <Edge podRows={pods} fairness={d.derived.fairness} />}
      </div>
    </section>
  );
}
