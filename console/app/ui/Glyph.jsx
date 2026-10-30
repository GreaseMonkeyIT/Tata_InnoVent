// Status glyph of the console (LOG-121). One shape per state, the same everywhere: on the floor, in
// the attention list, in the vitals strip and in the drawer rows (research_hud_references.md rule 4).
// Shape means severity only, never the machine type.
//   alarm    red square                 warn   orange filled triangle
//   recover  outline circle, heavy      ok     filled ink circle (running)
//   idle     outline circle, thin       busy   dashed circle (a person must act: load, change)
// The red and orange fills have a 1.5 px ink outline: alone they fail 3:1 on the light floor.
const NAMES = { alarm: "alarm", warn: "warning", recover: "recovering", ok: "running", idle: "idle", busy: "a person must act" };

export default function Glyph({ st = "idle", size = 12, title, blink = false }) {
  const o = { vectorEffect: "non-scaling-stroke" };
  return (
    <svg className={`glyph${blink ? " blink" : ""}`} width={size} height={size} viewBox="0 0 16 16" role="img" aria-label={title || NAMES[st]}>
      {st === "alarm" && <rect x="1.5" y="1.5" width="13" height="13" fill="var(--alarm)" stroke="var(--glyph-outline)" strokeWidth="1.5" {...o} />}
      {st === "warn" && <path d="M8 1.2 L15.2 14.6 H0.8 Z" fill="var(--warn)" stroke="var(--glyph-outline)" strokeWidth="1.5" strokeLinejoin="round" {...o} />}
      {st === "recover" && <circle cx="8" cy="8" r="5.8" fill="none" stroke="var(--ink-strong)" strokeWidth="2" {...o} />}
      {st === "ok" && <circle cx="8" cy="8" r="5" fill="var(--ink)" />}
      {st === "idle" && <circle cx="8" cy="8" r="4.6" fill="none" stroke="var(--ink)" strokeWidth="1.3" {...o} />}
      {st === "busy" && <circle cx="8" cy="8" r="4.6" fill="none" stroke="var(--ink)" strokeWidth="1.6" strokeDasharray="2.4 1.8" />}
    </svg>
  );
}
