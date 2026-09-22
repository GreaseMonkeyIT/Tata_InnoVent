"use client";
import { useState } from "react";
import Glyph from "./Glyph";
import { QST } from "./lib/palette";
import { fmtTag } from "./lib/format";

// 2F.2 SCADA tag browser. The industrial data path made visible: every tag traveled physics ->
// PLC register -> Modbus or S7comm -> tag server -> historian. An absent tag server says so.
export default function Tags({ scada }) {
  const [q, setQ] = useState("");
  const rows = scada?.tags || [];
  if (!scada) return <div className="empty">waiting for the tag server…</div>;
  // PS6 (SCENARIOS.md 2.7): a dead tag server is a blind SCADA view, not a quiet one. The engine's
  // plant plane still reads the physics tap, so the console says both things.
  if (scada.source === "unavailable") return <div className="empty">tag server unreachable · the SCADA view is blind · the physics tap is still live</div>;
  if (!rows.length) return <div className="empty">the tag server answers but holds no tags yet</div>;
  const f = q.trim().toLowerCase();
  const shown = f ? rows.filter((t) => `${t.tag} ${t.asset} ${t.signal} ${t.address}`.toLowerCase().includes(f)) : rows;
  const bad = rows.filter((t) => t.quality !== "GOOD").length;
  return (
    <div className="tags">
      <div className="tg-bar">
        <input className="tg-filter" value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter · asset, signal, address" aria-label="filter tags" />
        <span className="scada-badge">
          <Glyph st={scada.plc_connected ? "ok" : "hot"} size={7} />plc
          <Glyph st={scada.historian?.connected ? "ok" : "hot"} size={7} />historian
          <b>{scada.historian?.rows_per_s ?? 0} rows/s</b>
          <i>{(scada.historian?.rows_total ?? 0).toLocaleString("en-IN")} total</i>
          <i>{shown.length}/{rows.length} tags{bad ? ` · ${bad} not good` : ""}</i>
        </span>
      </div>
      <div className="tagstrip">
        {shown.map((t) => (
          <div key={t.tag} className={`tagchip${t.kind === "derived" ? " drv" : ""}`} title={`${t.kind} · ${t.address} · ${t.quality}`}>
            <Glyph st={QST[t.quality] || "idle"} size={7} />
            <span className="tn">{t.tag}</span>
            <span className="tv">{fmtTag(t)}</span>
            <span className="ta">{t.address}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
