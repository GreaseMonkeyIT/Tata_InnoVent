// Min/max-domained sparkline. Voltage lives in a narrow band far from 0, and a 0-based scale
// would flatten the sag the console exists to show. `fluid` stretches the line to its container,
// and the non-scaling stroke keeps the line width even when the SVG stretches.
export default function Spark({ hist, lo, hi, color, w = 92, h = 20, fluid = false }) {
  const n = hist.length;
  const box = { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none", className: fluid ? "spark fluid" : "spark" };
  const size = fluid ? {} : { width: w, height: h };
  if (n < 2) return <svg {...box} {...size} />;
  const min = lo != null ? lo : Math.min(...hist);
  const max = hi != null ? hi : Math.max(...hist);
  const span = max - min || 1;
  const xs = (i) => (i / (n - 1)) * (w - 2) + 1;
  const ys = (v) => h - 2 - Math.max(0, Math.min(1, (v - min) / span)) * (h - 4);
  let line = "";
  hist.forEach((v, i) => { line += (i ? "L" : "M") + xs(i).toFixed(1) + " " + ys(v).toFixed(1) + " "; });
  return (
    <svg {...box} {...size}>
      <path d={line} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
