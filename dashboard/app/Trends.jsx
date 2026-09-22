"use client";
import { useState } from "react";
import { DEV } from "./lib/api";

// Trends: the Grafana panels, served through the console's own origin at /grafana/ (nginx proxy,
// LOG-062). The console is HTTPS, and a browser blocks a plain-HTTP frame inside it as mixed
// content, so the frames must never point at the Grafana NodePort. The frames mount only while
// this tab is open, and only three at a time.
const SETS = {
  plant: { uid: "skn-plant", panels: [{ id: 1, cap: "Bus voltage · per rail" }, { id: 2, cap: "Current draw · per machine" }, { id: 3, cap: "Coolant temps · vs trip" }] },
  edge: { uid: "skn-psi", panels: [{ id: 1, cap: "PSI · I/O" }, { id: 2, cap: "PSI · CPU" }, { id: 3, cap: "PSI · memory" }] },
};
const src = (uid, id) =>
  `/grafana/d-solo/${uid}/${uid}?orgId=1&panelId=${id}&theme=dark&from=now-15m&to=now&refresh=5s&timezone=Asia/Kolkata`;

export default function Trends() {
  const [set, setSet] = useState("plant");
  const s = SETS[set];
  return (
    <div className="trends">
      <div className="tr-bar">
        <div className="seg" role="group" aria-label="trend set">
          <button className={set === "plant" ? "on" : ""} onClick={() => setSet("plant")}>plant</button>
          <button className={set === "edge" ? "on" : ""} onClick={() => setSet("edge")}>edge · psi</button>
        </div>
        <span className="lbl">grafana over prometheus · last 15 min · 12 h retained</span>
      </div>
      <div className="gframes">
        {s.panels.map((p) => (
          <div key={`${s.uid}-${p.id}`} className="gframe">
            <div className="cap">{p.cap}</div>
            {DEV
              ? <div className="gph">Grafana panel {s.uid} #{p.id}<br />loads on the box through /grafana/</div>
              : <iframe title={p.cap} src={src(s.uid, p.id)} />}
          </div>
        ))}
      </div>
    </div>
  );
}
