"use client";
import "./ui/styles/tokens.css";
import "./ui/styles/ui.css";
import dynamic from "next/dynamic";

// The VISR console (LOG-120). WebGL and the recording load in the browser only.
const UiApp = dynamic(() => import("./ui/UiApp"), { ssr: false });

export default function Page() {
  return <UiApp />;
}
