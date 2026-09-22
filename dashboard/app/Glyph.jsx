// Status glyph. The shape carries the state as well as the color (ISA-101 redundant coding), so
// the console stays readable for color-blind viewers and in a compressed video:
// filled circle = normal, triangle = warning, square = alarm or root cause,
// slashed ring = tripped, hollow ring = idle or unknown, dashed ring = starting.
const SHAPE = { ok: "ok", strained: "warn", warn: "warn", hot: "alarm", alarm: "alarm", trip: "trip", idle: "idle", busy: "busy" };
const COLOR = { ok: "var(--teal)", warn: "var(--amber)", alarm: "var(--red)", trip: "var(--red)", idle: "var(--text-faint)", busy: "var(--teal)" };

export default function Glyph({ st = "ok", size = 9, title }) {
  const s = SHAPE[st] || "idle";
  const c = COLOR[s];
  return (
    <svg className="glyph" width={size} height={size} viewBox="0 0 10 10" role="img" aria-label={title || s}>
      {title && <title>{title}</title>}
      {s === "ok" && <circle cx="5" cy="5" r="4" fill={c} />}
      {s === "warn" && <path d="M5 0.6 L9.6 9.2 H0.4 Z" fill={c} />}
      {s === "alarm" && <rect x="1" y="1" width="8" height="8" fill={c} />}
      {s === "trip" && <><circle cx="5" cy="5" r="3.7" fill="none" stroke={c} strokeWidth="1.6" /><path d="M2.2 7.8 L7.8 2.2" stroke={c} strokeWidth="1.6" /></>}
      {s === "idle" && <circle cx="5" cy="5" r="3.6" fill="none" stroke={c} strokeWidth="1.4" />}
      {s === "busy" && <circle cx="5" cy="5" r="3.6" fill="none" stroke={c} strokeWidth="1.8" strokeDasharray="3.6 2.2" />}
    </svg>
  );
}
