import localFont from "next/font/local";

// Each route loads its own stylesheet (LOG-120): the new console imports app/ui/styles, the legacy
// console (/legacy) imports globals.css and scifi.css. The root layout loads no CSS.
//
// The legacy console's display face, Industry (Fontfabric trial "Test" weights), self-hosted via
// next/font/local so the static export stays air-gap portable. Only /legacy uses it (--font-display);
// preload is off so the new console does not fetch it. It leaves with the legacy console.
const display = localFont({
  src: [
    { path: "./fonts/IndustryTest-Medium.otf", weight: "500", style: "normal" },
    { path: "./fonts/IndustryTest-Demi.otf", weight: "600", style: "normal" },
    { path: "./fonts/IndustryTest-Bold.otf", weight: "700", style: "normal" },
    { path: "./fonts/IndustryTest-Black.otf", weight: "800", style: "normal" },
  ],
  variable: "--font-display",
  display: "swap",
  preload: false,
});

export const metadata = {
  title: "VISR",
  description: "Plant operator console: the floor, the vitals and the verdict.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={display.variable}>
      <body>{children}</body>
    </html>
  );
}
