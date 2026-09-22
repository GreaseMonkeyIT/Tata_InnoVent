"use client";
import { useState } from "react";
import Boot from "./Boot";
import Console from "./Console";
import useConsoleData from "./lib/useConsoleData";
import { getJSON } from "./lib/api";

// VISR operator console. The Boot overlay runs its real self-check on every full load (2D-1),
// and the console renders under it, so the data is ready when the operator enters.
export default function Page() {
  const d = useConsoleData();
  const [booting, setBooting] = useState(true);
  return (
    <>
      {booting && <Boot getJSON={getJSON} onDone={() => setBooting(false)} />}
      <Console d={d} />
    </>
  );
}
