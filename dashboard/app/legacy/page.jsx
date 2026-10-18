"use client";
import "../globals.css";
import "../scifi.css";   // the digital-twin theme layer: loaded after globals.css, overrides only what it needs
import { useState } from "react";
import Boot from "../Boot";
import Console from "../Console";
import useConsoleData from "../lib/useConsoleData";
import { getJSON } from "../lib/api";

// The previous VISR operator console (LOG-062 to LOG-105), kept unchanged at /legacy until the
// operator picks a prototype of the new console (LOG-120). It still runs on the old plant API and
// its dev mock (?mock=). The Boot overlay runs its real self-check on every full load (2D-1).
export default function LegacyPage() {
  const d = useConsoleData();
  const [booting, setBooting] = useState(true);
  return (
    <>
      {booting && <Boot getJSON={getJSON} onDone={() => setBooting(false)} />}
      <Console d={d} />
    </>
  );
}
