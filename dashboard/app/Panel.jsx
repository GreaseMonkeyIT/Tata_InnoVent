// One console panel: a fixed header strip and a body. Only the body scrolls, so a panel never
// moves or resizes with its data. `flush` removes the body padding (the map well uses it).
export default function Panel({ id, title, meta, tools, flush = false, children }) {
  return (
    <section className={`pnl ${id || ""}`} aria-label={title}>
      <header className="pnl-h">
        <h2 className="pnl-t">{title}</h2>
        {meta != null && meta !== "" && <div className="pnl-m">{meta}</div>}
        {tools && <div className="pnl-tools">{tools}</div>}
      </header>
      <div className={`pnl-b${flush ? " flush" : ""}`}>{children}</div>
    </section>
  );
}
