import "./globals.css";
import localFont from "next/font/local";

// VISR display face — Industry (Fontfabric), self-hosted via next/font/local so the static export
// stays air-gap portable (no CDN). Used for HUD chrome (brand, section heads, gauges, micro-labels);
// body/data text stays Inter. Trial ("Test") weights — swap to licensed files in app/fonts when ready.
const display = localFont({
  src: [
    { path: "./fonts/IndustryTest-Medium.otf", weight: "500", style: "normal" },
    { path: "./fonts/IndustryTest-Demi.otf", weight: "600", style: "normal" },
    { path: "./fonts/IndustryTest-Bold.otf", weight: "700", style: "normal" },
    { path: "./fonts/IndustryTest-Black.otf", weight: "800", style: "normal" },
  ],
  variable: "--font-display",
  display: "swap",
});

export const metadata = {
  title: "VISR · Causal AIOps",
  description: "Causal correlation verdict for a single-node industrial edge stack.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={display.variable}>
      <body>{children}</body>
    </html>
  );
}
