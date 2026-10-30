// Each route loads its own stylesheet (LOG-120): the console imports app/ui/styles. The root layout
// loads no CSS and no web font: the console uses the system faces of tokens.css (Segoe UI, Arial).

export const metadata = {
  title: "VISR",
  description: "Plant operator console: the floor, the vitals and the verdict.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
