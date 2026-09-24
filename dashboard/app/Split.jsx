"use client";
import { useRef } from "react";

// A drag handle between two panels (LOG-081). The operator drags it to give a panel more room,
// and a double-click returns that size to the default. `size` names the layout key it sets, and
// `of` says which element it measures: the panel before it, the panel after it, or the column.
// `sign` is +1 when the measured element grows with the drag, -1 when it shrinks.
export default function Split({ dir, size, of, sign = 1, min = 120, max = 2000, onSize, onReset }) {
  const drag = useRef(null);
  const target = (el) => (of === "prev" ? el.previousElementSibling : of === "next" ? el.nextElementSibling : el.parentElement);

  function down(e) {
    const el = target(e.currentTarget);
    if (!el) return;
    const r = el.getBoundingClientRect();
    drag.current = { p: dir === "v" ? e.clientX : e.clientY, start: dir === "v" ? r.width : r.height };
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault();
  }
  function move(e) {
    if (!drag.current) return;
    const d = (dir === "v" ? e.clientX : e.clientY) - drag.current.p;
    onSize(size, Math.round(Math.max(min, Math.min(max, drag.current.start + sign * d))));
  }
  function up(e) {
    if (!drag.current) return;
    drag.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    onSize(size, null, true);
  }

  return (
    <div className={`split ${dir}`} role="separator" aria-orientation={dir === "v" ? "vertical" : "horizontal"}
      title="drag to resize · double-click to reset"
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
      onDoubleClick={() => onReset(size)} />
  );
}
