"use client";
import { useState } from "react";

// The brand strip at the left of the command bar: the VISR plate, the company logo, then two short
// lines (the plant name, the programme). The site and the twin line live in the map's `info` overlay.
//
// The logo files live in dashboard/public/brand/ (a Next static export serves ONLY that folder, at the
// site root, so the browser path is /brand/...). Two files, both made from the official PNG:
//   tata-technologies.png        the colour logo on a transparent background
//   tata-technologies-white.png  the reversed (all-white) logo for the dark ground
// LOGO_VARIANT picks how it is shown. If a file is missing, the text wordmark shows instead.
export const PLANT = {
  name: "Stamping & machining hall",
  site: "Plant 2 · Pune",
  twin: "Digital twin · edge node forge",
};
// "reversed": the white logo straight on the dark ground (the cleanest look)
// "plate":    the colour logo on a small white plate (the safe choice under strict brand rules)
export const LOGO_VARIANT = "reversed";

export function Logo({ height = 34, variant = LOGO_VARIANT }) {
  const [ok, setOk] = useState(true);
  if (!ok) return <span className="brand-tt">Tata Technologies</span>;
  const src = variant === "reversed" ? "/brand/tata-technologies-white.png" : "/brand/tata-technologies.png";
  return <img className={`brand-logo ${variant}`} style={{ height }} src={src} alt="Tata Technologies" onError={() => setOk(false)} />;
}

export default function Brand({ compact = false }) {
  return (
    <div className="brand">
      <span className="brand-plate">VISR</span>
      <Logo height={34} />
      {!compact && (
        <div className="brand-id">
          <div className="brand-plant">{PLANT.name}</div>
          <div className="brand-prog"><span className="brand-ev">Tata InnoVent 2026</span></div>
        </div>
      )}
    </div>
  );
}
