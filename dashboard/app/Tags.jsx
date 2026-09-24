"use client";
import { useState } from "react";
import Glyph from "./Glyph";
import { QST } from "./lib/palette";
import { fmtTag } from "./lib/format";

// 2F.2 SCADA tag browser. The industrial data path made visible: every tag traveled physics ->
// PLC register -> Modbus or S7comm -> tag server -> historian. An absent tag server says so.
// One row per tag in a scrolling table with a fixed header (LOG-082). The filter matches the tag,
// the asset, the signal, and the address. A derived tag shows "calc" in the address column, and
// its formula is in the row tooltip.
export default function Tags({ scada }) {
  const [q, setQ] = useState("");
  const rows = scada?.tags || [];
  if (!scada) return <div className="empty">waiting for the tag server…</div>;
  // PS6 (SCENARIOS.md 2.7): a dead tag server is a blind SCADA view, not a quiet one.
  if (scada.source === "unavailable") return <div className="empty">tag server unreachable · SCADA blind</div>;
  if (!rows.length) return <div className="empty">no tags</div>;
  const f = q.trim().toLowerCase();
  const shown = f ? rows.filter((t) => `${t.tag} ${t.asset} ${t.signal} ${t.address}`.toLowerCase().includes(f)) : rows;
  const bad = rows.filter((t) => t.quality !== "GOOD").length;
  return (
    <div className="tags">
      <div className="tg-bar">
        <input className="tg-filter" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="search tags" aria-label="search tags" />
        <span className="tg-count">{shown.length}/{rows.length}{bad ? <b> · {bad} not good</b> : null}</span>
        <span className="scada-badge">
          <Glyph st={scada.plc_connected ? "ok" : "hot"} size={7} />plc
          <Glyph st={scada.historian?.connected ? "ok" : "hot"} size={7} />historian
        </span>
      </div>
      <div className="tg-wrap">
        <table className="tg-table">
          <thead>
            <tr><th className="q" /><th>tag</th><th className="v">value</th><th>quality</th><th>address</th></tr>
          </thead>
          <tbody>
            {shown.map((t) => {
              const drv = t.kind === "derived";
              return (
                <tr key={t.tag} className={`qs-${String(t.quality || "").toLowerCase()}`} title={`${t.tag} · ${t.kind} · ${t.address}`}>
                  <td className="q"><Glyph st={QST[t.quality] || "idle"} size={7} /></td>
                  <td className="n">{t.tag}</td>
                  <td className="v">{fmtTag(t)}</td>
                  <td className="s">{t.quality}</td>
                  <td className="a">{drv ? "calc" : t.address}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!shown.length && <div className="empty">no tag matches "{q}"</div>}
      </div>
    </div>
  );
}
