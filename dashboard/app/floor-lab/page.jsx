"use client";
import dynamic from "next/dynamic";

// The floor prototype is WebGL (it touches window), so it renders client-only.
const FloorLab = dynamic(() => import("./FloorLab"), { ssr: false });

export default function Page() {
  return <FloorLab />;
}
